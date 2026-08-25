"""Feedback routes.

Lists client feedback entries and lets authorized users update feedback/status.
When status changes, concerned teams are notified.
"""

from flask import Blueprint, request

from auth.middleware import token_required
from common.constants import BROAD_ACCESS_ROLES, DEPARTMENTS, SHOT_STATUSES
from common.db_utils import create_notification, get_user, run_query
from common.http import failure, success
from common.serializers import SHOT_SELECT, shot_to_json

feedback_bp = Blueprint("feedback", __name__)


def _accessible_departments(user):
    if not user:
        return []
    if user["role"] in BROAD_ACCESS_ROLES:
        return DEPARTMENTS
    return [user["department"]] if user["department"] in DEPARTMENTS else []


def _can_access(user, department):
    if not user:
        return False
    if user["role"] in BROAD_ACCESS_ROLES:
        return True
    # department may be a comma-separated list (multi-department shot).
    depts = [d.strip() for d in (department or "").split(",") if d.strip()]
    return user["department"] in depts


def _notify_concern_team(shot, current_user_id, message):
    recipients = set()

    artist_id = shot.get("artist_id")
    if artist_id:
        recipients.add(artist_id)

    dept_parts = [d.strip() for d in (shot.get("department") or "").split(",") if d.strip()]
    if dept_parts:
        placeholders = ", ".join(["%s"] * len(dept_parts))
        dept_rows = run_query(
            f"""
            SELECT user_id FROM users
            WHERE department IN ({placeholders}) AND role IN ('Supervisor', 'Team Lead') AND status = 'Active'
            """,
            tuple(dept_parts),
            fetch_all=True,
        ) or []
        for row in dept_rows:
            recipients.add(row["user_id"])

    broad_rows = run_query(
        """
        SELECT user_id FROM users
        WHERE role IN ('Admin', 'Production', 'Management') AND status = 'Active'
        """,
        fetch_all=True,
    ) or []
    for row in broad_rows:
        recipients.add(row["user_id"])

    if current_user_id:
        recipients.discard(current_user_id)

    for user_id in recipients:
        create_notification(message, "Status Updated", user_id)


@feedback_bp.route("/client", methods=["GET"])
@token_required
def list_client_feedback(current_user_id):
    user = get_user(current_user_id)
    allowed_departments = _accessible_departments(user)
    if not allowed_departments:
        return failure("You do not have access to any department.", 403)

    department = request.args.get("department")
    client_id = request.args.get("clientId")
    show_id = request.args.get("showId")
    status = request.args.get("status")

    clauses = ["COALESCE(s.client_feedback, '') <> ''"]
    params = []

    if department:
        requested = [d.strip() for d in department.split(",") if d.strip()]
        if not requested or any(d not in allowed_departments for d in requested):
            return failure("You are not allowed to access this department.", 403)
        dept_clause = " OR ".join(["FIND_IN_SET(%s, s.department)"] * len(requested))
        clauses.append(f"({dept_clause})")
        params.extend(requested)
    else:
        dept_clause = " OR ".join(["FIND_IN_SET(%s, s.department)"] * len(allowed_departments))
        clauses.append(f"({dept_clause})")
        params.extend(allowed_departments)

    if client_id:
        clauses.append("sh.client_id = %s")
        params.append(client_id)
    if show_id:
        clauses.append("s.show_id = %s")
        params.append(show_id)
    if status:
        clauses.append("s.status = %s")
        params.append(status)

    where = " WHERE " + " AND ".join(clauses)
    rows = run_query(
        SHOT_SELECT + where + " ORDER BY s.department, sh.show_name, s.shot_code",
        tuple(params),
        fetch_all=True,
    ) or []
    return success({"feedbacks": [shot_to_json(row) for row in rows]})


@feedback_bp.route("/shots/<shot_id>", methods=["PATCH"])
@token_required
def update_feedback(current_user_id, shot_id):
    existing = run_query(
        "SELECT shot_id, department, shot_code, status, artist_id FROM shots WHERE shot_id = %s",
        (shot_id,),
        fetch_one=True,
    )
    if not existing:
        return failure("Shot not found", 404)

    user = get_user(current_user_id)
    if not _can_access(user, existing["department"]):
        return failure("You are not allowed to modify this department.", 403)

    data = request.get_json(silent=True) or {}
    sets = []
    params = []

    status_changed = False
    new_status = data.get("status")
    if new_status is not None:
        if new_status not in SHOT_STATUSES:
            return failure("Invalid status.", 400)
        sets.append("status = %s")
        params.append(new_status)
        status_changed = new_status != existing["status"]

    if "clientFeedback" in data:
        sets.append("client_feedback = %s")
        params.append(data.get("clientFeedback"))

    if not sets:
        return failure("No fields to update.", 400)

    params.append(shot_id)
    run_query(f"UPDATE shots SET {', '.join(sets)} WHERE shot_id = %s", tuple(params))

    if status_changed:
        actor_name = user["name"] if user else "A team member"
        _notify_concern_team(
            existing,
            current_user_id,
            f"{actor_name} changed shot {existing['shot_code']} status to '{new_status}'.",
        )

    row = run_query(SHOT_SELECT + " WHERE s.shot_id = %s", (shot_id,), fetch_one=True)
    return success({"shot": shot_to_json(row), "statusChanged": status_changed})
