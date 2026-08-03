import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from flask import Blueprint, request
from auth.middleware import token_required
from common.constants import (
    ARTIST_LEVELS,
    ARTIST_STATUSES,
    BROAD_ACCESS_ROLES,
    DEPARTMENTS,
    SHOT_STATUSES,
    SUPERVISOR_STATUSES,
    USER_DEPARTMENTS,
    USER_ROLES,
)
from common.db_utils import generate_prefixed_id, get_user, initials, run_query
from common.http import failure, success
from common.options_store import (
    ARTIST_LEVEL_CATEGORY,
    ARTIST_STATUS_CATEGORY,
    BROAD_ACCESS_ROLE_CATEGORY,
    DEPARTMENT_CATEGORY,
    PIPELINE_DEPARTMENT_CATEGORY,
    ROLE_CATEGORY,
    SHOT_STATUS_CATEGORY,
    SUPERVISOR_STATUS_CATEGORY,
    list_options,
    seed_options,
    upsert_option,
)
from database.connection import get_db_cursor

auth_bp = Blueprint("auth", __name__)


def _distinct_values(table_name, column_name):
    allowed = {
        ("users", "role"),
        ("users", "department"),
        ("users", "level"),
        ("shots", "department"),
        ("shots", "status"),
        ("shots", "supervisor_status"),
        ("shots", "artist_status"),
    }
    if (table_name, column_name) not in allowed:
        return []

    rows = run_query(
        f"""
        SELECT DISTINCT {column_name} AS value
        FROM {table_name}
        WHERE {column_name} IS NOT NULL AND TRIM({column_name}) <> ''
        ORDER BY {column_name}
        """
    )
    return [(r.get("value") or "").strip() for r in rows if (r.get("value") or "").strip()]


def _merge_ordered(defaults, dynamic_values):
    ordered = []
    seen = set()
    for value in list(defaults) + list(dynamic_values):
        if not value or value in seen:
            continue
        ordered.append(value)
        seen.add(value)
    return ordered


def _sign_token(user_id: str, role: str) -> str:
    payload = {
        "user_id": user_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=int(os.getenv("JWT_EXPIRATION_HOURS", 24))),
    }
    return jwt.encode(payload, os.getenv("SECRET_KEY"), algorithm="HS256")


@auth_bp.route("/options", methods=["GET"])
def options():
    """Return dynamic app options for forms and workflow dropdowns.

    Cached for 5 minutes.  Pass ?refresh=1 to force a cache miss.
    This endpoint does ~14 DB round-trips without cache — caching eliminates that.
    """
    from app import cache  # lazy import — app must be created first

    force_refresh = request.args.get("refresh") == "1"
    cache_key = "app_options"
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    seed_options(ROLE_CATEGORY, USER_ROLES)
    seed_options(DEPARTMENT_CATEGORY, USER_DEPARTMENTS)
    seed_options(PIPELINE_DEPARTMENT_CATEGORY, DEPARTMENTS)
    seed_options(ARTIST_LEVEL_CATEGORY, ARTIST_LEVELS)
    seed_options(SHOT_STATUS_CATEGORY, SHOT_STATUSES)
    seed_options(SUPERVISOR_STATUS_CATEGORY, SUPERVISOR_STATUSES)
    seed_options(ARTIST_STATUS_CATEGORY, ARTIST_STATUSES)
    seed_options(BROAD_ACCESS_ROLE_CATEGORY, BROAD_ACCESS_ROLES)

    roles = _merge_ordered(
        USER_ROLES,
        list_options(ROLE_CATEGORY) + _distinct_values("users", "role"),
    )
    departments = _merge_ordered(
        USER_DEPARTMENTS,
        list_options(DEPARTMENT_CATEGORY) + _distinct_values("users", "department"),
    )
    pipeline_departments = _merge_ordered(
        DEPARTMENTS,
        list_options(PIPELINE_DEPARTMENT_CATEGORY)
        + _distinct_values("shots", "department"),
    )
    artist_levels = _merge_ordered(
        ARTIST_LEVELS,
        list_options(ARTIST_LEVEL_CATEGORY) + _distinct_values("users", "level"),
    )
    shot_statuses = _merge_ordered(
        SHOT_STATUSES,
        list_options(SHOT_STATUS_CATEGORY) + _distinct_values("shots", "status"),
    )
    supervisor_statuses = _merge_ordered(
        SUPERVISOR_STATUSES,
        list_options(SUPERVISOR_STATUS_CATEGORY)
        + _distinct_values("shots", "supervisor_status"),
    )
    artist_statuses = _merge_ordered(
        ARTIST_STATUSES,
        list_options(ARTIST_STATUS_CATEGORY)
        + _distinct_values("shots", "artist_status"),
    )
    broad_access_roles = _merge_ordered(
        BROAD_ACCESS_ROLES,
        list_options(BROAD_ACCESS_ROLE_CATEGORY),
    )

    payload = success(
        {
            "roles": roles,
            "departments": departments,
            "pipelineDepartments": pipeline_departments,
            "artistLevels": artist_levels,
            "shotStatuses": shot_statuses,
            "supervisorStatuses": supervisor_statuses,
            "artistStatuses": artist_statuses,
            "broadAccessRoles": broad_access_roles,
        }
    )
    cache.set(cache_key, payload, timeout=300)
    return payload


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return failure("Email and password are required", 400)

    user = run_query(
        "SELECT * FROM users WHERE LOWER(email) = %s", (email,), fetch_one=True
    )
    if not user:
        return failure("Invalid email address. User not found.", 401)

    if user["status"] == "Disabled":
        return failure("This account has been disabled. Please contact the administrator.", 403)

    if not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        return failure("Invalid password.", 401)

    token = _sign_token(user["user_id"], user["role"])
    user_response = {
        "userId": user["user_id"],
        "name": user["name"],
        "email": user["email"],
        "department": user["department"],
        "role": user["role"],
        "status": user["status"],
        "avatar": user["avatar"],
    }
    return success({"token": token, "user": user_response})


@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    full_name = data.get("fullName", "").strip()
    email = data.get("email", "").strip().lower()
    phone = data.get("phone", "").strip()
    employee_id = data.get("employeeId", "").strip()
    department = data.get("department", "").strip()
    role = data.get("role", "Employee").strip() or "Employee"
    password = data.get("password", "")

    if not all([full_name, email, password, department]):
        return failure("Full name, email, password, and department are required.", 400)

    upsert_option(ROLE_CATEGORY, role)
    upsert_option(DEPARTMENT_CATEGORY, department)

    existing = run_query(
        "SELECT user_id FROM users WHERE LOWER(email) = %s", (email,), fetch_one=True
    )
    if existing:
        return failure("Email is already registered.", 409)

    user_id = generate_prefixed_id("users", "user_id", "USR", 100)
    avatar = initials(full_name)
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    run_query(
        """
        INSERT INTO users (user_id, name, email, department, password_hash, role, status, avatar, phone, employee_id_ext)
        VALUES (%s, %s, %s, %s, %s, %s, 'Active', %s, %s, %s)
        """,
        (user_id, full_name, email, department, password_hash, role, avatar, phone, employee_id),
    )
    run_query("INSERT INTO user_settings (user_id) VALUES (%s)", (user_id,))

    token = _sign_token(user_id, role)
    return success(
        {
            "token": token,
            "user": {
                "userId": user_id,
                "name": full_name,
                "email": email,
                "department": department,
                "role": role,
                "status": "Active",
                "avatar": avatar,
            },
        },
        201,
    )


@auth_bp.route("/logout", methods=["POST"])
@token_required
def logout(_current_user_id):
    return success({"message": "Logged out successfully"})


@auth_bp.route("/me", methods=["GET"])
@token_required
def me(current_user_id):
    user = get_user(current_user_id)
    if not user:
        return failure("User not found", 404)

    return success(
        {
            "user": {
                "userId": user["user_id"],
                "name": user["name"],
                "email": user["email"],
                "department": user["department"],
                "role": user["role"],
                "status": user["status"],
                "avatar": user["avatar"],
            }
        }
    )
