import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from flask import Blueprint, request
from auth.middleware import token_required
from common.audit import write_activity_log
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
        """,
        fetch_all=True,
    ) or []
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
    # Cache lives in its own module so it is imported under exactly one name
    # even when app.py is loaded as both __main__ and app (python app.py).
    from common.cache_instance import cache

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
    # shots.department may hold a comma-separated list (multi-department
    # shots) — split it so every department appears as its own option.
    raw_pipeline_values = _distinct_values("shots", "department")
    split_pipeline_values = []
    for value in raw_pipeline_values:
        for part in value.split(","):
            part = part.strip()
            if part:
                split_pipeline_values.append(part)
    pipeline_departments = _merge_ordered(
        DEPARTMENTS,
        list_options(PIPELINE_DEPARTMENT_CATEGORY) + split_pipeline_values,
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


@auth_bp.route("/departments", methods=["POST"])
@token_required
def add_department(current_user_id):
    """Create a new department in both the user and pipeline lists.

    Restricted to broad-access roles (Admin / Production / Management).
    The department is added to the ``department`` (registration) and
    ``pipelineDepartment`` (shot workflow) option categories so it shows up
    everywhere.  The cached ``/auth/options`` response is busted so the new
    department is picked up immediately.
    """
    user = get_user(current_user_id)
    if not user or user["role"] not in BROAD_ACCESS_ROLES:
        return failure("You are not allowed to add departments.", 403)

    data = request.get_json(silent=True) or {}
    department = (data.get("department") or data.get("name") or "").strip()
    if not department:
        return failure("department is required.", 400)

    upsert_option(DEPARTMENT_CATEGORY, department)
    upsert_option(PIPELINE_DEPARTMENT_CATEGORY, department)

    # Bust the /auth/options cache so all screens see the new department
    # without waiting out the 5-minute TTL.
    from common.cache_instance import cache

    cache.delete("app_options")
    write_activity_log(
        current_user_id,
        "Auth",
        "CREATE",
        "Department",
        department,
        {},
    )

    return success({"department": department}, 201)


@auth_bp.route("/roles", methods=["POST"])
@token_required
def add_role(current_user_id):
    """Create a new role in the registration / access options.

    Restricted to broad-access roles (Admin / Production / Management).
    The role is added to the ``role`` option category so it shows up in the
    registration dropdown and the Access Provider matrix.  The cached
    ``/auth/options`` response is busted so the new role is picked up
    immediately.
    """
    user = get_user(current_user_id)
    if not user or user["role"] not in BROAD_ACCESS_ROLES:
        return failure("You are not allowed to add roles.", 403)

    data = request.get_json(silent=True) or {}
    role = (data.get("role") or data.get("name") or "").strip()
    if not role:
        return failure("role is required.", 400)

    upsert_option(ROLE_CATEGORY, role)

    # Bust the /auth/options cache so all screens see the new role without
    # waiting out the 5-minute TTL.
    from common.cache_instance import cache

    cache.delete("app_options")
    write_activity_log(
        current_user_id,
        "Auth",
        "CREATE",
        "Role",
        role,
        {},
    )

    return success({"role": role}, 201)


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
    write_activity_log(
        user["user_id"],
        "Auth",
        "LOGIN",
        "User",
        user["user_id"],
        {"email": email, "role": user["role"]},
    )
    user_response = {
        "userId": user["user_id"],
        "name": user["name"],
        "email": user["email"],
        "department": user["department"],
        "role": user["role"],
        "status": user["status"],
        "avatar": user["avatar"],
        "level": user.get("level"),
        "phone": user.get("phone"),
        "employeeId": user.get("employee_id_ext"),
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
    # Register runs pre-login (no token), so the add-department endpoint may
    # not be callable here.  Persist the chosen department as a pipeline
    # department too so it appears in all import sections.
    upsert_option(PIPELINE_DEPARTMENT_CATEGORY, department)
    from common.cache_instance import cache
    cache.delete("app_options")

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
    write_activity_log(
        user_id,
        "Auth",
        "CREATE",
        "User",
        user_id,
        {"name": full_name, "email": email, "department": department, "role": role},
    )

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
                "level": None,
                "phone": phone,
                "employeeId": employee_id,
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
                "level": user.get("level"),
                "phone": user.get("phone"),
                "employeeId": user.get("employee_id_ext"),
            }
        }
    )


def _serialize_user(user: dict) -> dict:
    """Full user payload shared by /me and /profile endpoints."""
    return {
        "userId": user["user_id"],
        "name": user["name"],
        "email": user["email"],
        "department": user["department"],
        "role": user["role"],
        "status": user["status"],
        "avatar": user["avatar"],
        "level": user.get("level"),
        "phone": user.get("phone"),
        "employeeId": user.get("employee_id_ext"),
    }


def _serialize_teammate(row: dict) -> dict:
    """Compact user payload for senior list / manager card."""
    return {
        "userId": row["user_id"],
        "name": row["name"],
        "department": row.get("department"),
        "role": row.get("role"),
        "level": row.get("level"),
        "avatar": row.get("avatar"),
    }


@auth_bp.route("/profile", methods=["GET"])
@token_required
def profile(current_user_id):
    """Return the current user's profile plus department seniors and manager.

    - ``user``: the authenticated user (all editable fields).
    - ``seniors``: Active users in the same department with level 'Senior'.
    - ``manager``: Active Supervisor / Team Lead in the same department (or None).
    """
    user = get_user(current_user_id)
    if not user:
        return failure("User not found", 404)

    department = user.get("department") or ""

    seniors = run_query(
        """
        SELECT user_id, name, department, role, level, avatar
        FROM users
        WHERE status = 'Active'
          AND level = 'Senior'
          AND department = %s
          AND user_id != %s
        ORDER BY name
        """,
        (department, current_user_id),
        fetch_all=True,
    ) or []

    manager = run_query(
        """
        SELECT user_id, name, department, role, level, avatar
        FROM users
        WHERE status = 'Active'
          AND department = %s
          AND role IN ('Supervisor', 'Team Lead')
          AND user_id != %s
        ORDER BY FIELD(role, 'Supervisor', 'Team Lead'), name
        LIMIT 1
        """,
        (department, current_user_id),
        fetch_one=True,
    )

    return success(
        {
            "user": _serialize_user(user),
            "seniors": [_serialize_teammate(s) for s in seniors],
            "manager": _serialize_teammate(manager) if manager else None,
        }
    )


@auth_bp.route("/profile", methods=["PUT"])
@token_required
def update_profile(current_user_id):
    """Update the current user's own profile.

    Editable: name, email, phone, employeeId, avatar, level, and optional
    password change.  Role and department are NEVER editable by the user —
    any submitted values for them are ignored.
    """
    from common.db_utils import invalidate_user_cache

    user = get_user(current_user_id)
    if not user:
        return failure("User not found", 404)

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    phone = (data.get("phone") or "").strip() or None
    employee_id = (data.get("employeeId") or "").strip() or None
    avatar = (data.get("avatar") or "").strip()
    level = (data.get("level") or "").strip() or None
    current_password = data.get("currentPassword") or ""
    new_password = data.get("newPassword") or ""

    if not name or not email:
        return failure("Name and email are required.", 400)

    if level and level not in ARTIST_LEVELS:
        return failure(f"Invalid level. Choose from: {', '.join(ARTIST_LEVELS)}.", 400)

    if len(email) > 150:
        return failure("Email is too long.", 400)

    # Email uniqueness (excluding self)
    existing = run_query(
        "SELECT user_id FROM users WHERE LOWER(email) = %s AND user_id != %s",
        (email, current_user_id),
        fetch_one=True,
    )
    if existing:
        return failure("Email is already registered to another user.", 409)

    password_hash = user["password_hash"]
    if new_password:
        if not current_password:
            return failure("Current password is required to set a new password.", 400)
        if not bcrypt.checkpw(
            current_password.encode("utf-8"),
            password_hash.encode("utf-8"),
        ):
            return failure("Current password is incorrect.", 400)
        password_hash = bcrypt.hashpw(
            new_password.encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8")

    if not avatar:
        avatar = initials(name)

    run_query(
        """
        UPDATE users
        SET name = %s, email = %s, phone = %s, employee_id_ext = %s,
            avatar = %s, level = %s, password_hash = %s
        WHERE user_id = %s
        """,
        (name, email, phone, employee_id, avatar, level, password_hash, current_user_id),
    )
    invalidate_user_cache(current_user_id)
    write_activity_log(
        current_user_id,
        "Auth",
        "UPDATE",
        "Profile",
        current_user_id,
        {
            "name": name,
            "email": email,
            "phone": phone,
            "level": level,
            "passwordChanged": bool(new_password),
        },
    )

    updated = get_user(current_user_id)
    return success({"user": _serialize_user(updated)})
