"""Teams routes.

Lists all artists grouped under their respective department (ROTO, PAINT, MM,
COMP) along with their experience level, to give visibility of team structure.
"""

from flask import Blueprint, request

import bcrypt

from auth.middleware import token_required
from common.constants import (
    ARTIST_LEVELS,
    BROAD_ACCESS_ROLES,
    DEPARTMENTS,
    USER_DEPARTMENTS,
)
from common.db_utils import generate_prefixed_id, get_user, initials, run_query
from common.http import failure, success

teams_bp = Blueprint("teams", __name__)


def _known_departments():
    rows = run_query(
        """
        SELECT DISTINCT department
        FROM users
        WHERE department IS NOT NULL AND TRIM(department) <> ''
        ORDER BY department
        """,
        fetch_all=True,
    ) or []
    dynamic_departments = [
        (r.get("department") or "").strip()
        for r in rows
        if (r.get("department") or "").strip()
    ]

    ordered = []
    seen = set()
    for dept in USER_DEPARTMENTS + dynamic_departments:
        if not dept or dept in seen:
            continue
        ordered.append(dept)
        seen.add(dept)
    return ordered


def _serialize(row):
    return {
        "userId": row["user_id"],
        "name": row["name"],
        "department": row["department"],
        "role": row["role"],
        "level": row["level"],
        "avatar": row["avatar"],
    }


@teams_bp.route("", methods=["GET"])
@teams_bp.route("/", methods=["GET"])
@token_required
def teams(current_user_id):
    """Return artists grouped by department. 
    
    Optional ?department= filter.
    
    Access Control:
    - Admin/Production/Management: see all departments
    - Supervisor/Team Lead/Artist: see only their own department
    """
    user = get_user(current_user_id)
    requested_department = (request.args.get("department") or "").strip() or None
    restricted_department = None
    if user and user["role"] not in BROAD_ACCESS_ROLES:
        restricted_department = user.get("department")
        if requested_department and requested_department != restricted_department:
            return failure("You are not allowed to view this department.", 403)
    
    # Build the WHERE clause
    query = """
        SELECT user_id, name, department, role, level, avatar
        FROM users
        WHERE status = 'Active'
    """
    params = []

    effective_department = restricted_department or requested_department
    if effective_department:
        query += " AND department = %s"
        params.append(effective_department)

    query += " ORDER BY department, FIELD(level, 'Senior', 'Mid', 'Junior'), name"

    rows = run_query(query, tuple(params), fetch_all=True) or []

    if effective_department:
        department_order = [effective_department]
    elif restricted_department:
        department_order = [restricted_department]
    else:
        department_order = _known_departments()
        if not department_order:
            department_order = DEPARTMENTS

    departments = []
    for dept in department_order:
        members = [_serialize(r) for r in rows if r["department"] == dept]
        departments.append({"department": dept, "members": members})

    return success({"departments": departments})


@teams_bp.route("", methods=["POST"])
@teams_bp.route("/", methods=["POST"])
@token_required
def add_member(current_user_id):
    """Create a new team member (artist). Restricted to broad-access roles."""
    user = get_user(current_user_id)
    if not user or user["role"] not in BROAD_ACCESS_ROLES:
        return failure("You are not allowed to add team members.", 403)

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    department = (data.get("department") or "").strip()
    role = (data.get("role") or "Artist").strip() or "Artist"
    level = (data.get("level") or "").strip() or None
    password = data.get("password") or "password123"

    if not all([name, email, department]):
        return failure("name, email and department are required.", 400)
    if not role:
        return failure("Invalid role.", 400)
    if level and level not in ARTIST_LEVELS:
        return failure("Invalid level.", 400)

    existing = run_query(
        "SELECT user_id FROM users WHERE LOWER(email) = %s", (email,), fetch_one=True
    )
    if existing:
        return failure("Email is already registered.", 409)

    user_id = generate_prefixed_id("users", "user_id", "USR", 100)
    avatar = initials(name)
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    run_query(
        """
        INSERT INTO users (user_id, name, email, department, password_hash, role, level, status, avatar)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'Active', %s)
        """,
        (user_id, name, email, department, password_hash, role, level, avatar),
    )
    run_query("INSERT INTO user_settings (user_id) VALUES (%s)", (user_id,))

    return success(
        {
            "member": {
                "userId": user_id,
                "name": name,
                "department": department,
                "role": role,
                "level": level,
                "avatar": avatar,
            }
        },
        201,
    )


@teams_bp.route("/<user_id>", methods=["DELETE"])
@token_required
def remove_member(current_user_id, user_id):
    """Remove a team member. Restricted to broad-access roles."""
    actor = get_user(current_user_id)
    if not actor or actor["role"] not in BROAD_ACCESS_ROLES:
        return failure("You are not allowed to remove team members.", 403)
    if user_id == current_user_id:
        return failure("You cannot remove your own account.", 400)

    member = get_user(user_id)
    if not member:
        return failure("Team member not found", 404)

    # Unassign and reset any shots currently allocated to this member.
    # (the artist_id FK is ON DELETE SET NULL, but we reset the status too.)
    run_query(
        "UPDATE shots SET artist_status = 'YTS', artist_bid = 0, allocated_date = NULL WHERE artist_id = %s",
        (user_id,),
    )
    run_query("DELETE FROM users WHERE user_id = %s", (user_id,))

    return success({"message": "Team member removed", "userId": user_id})
