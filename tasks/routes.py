"""Tasks routes.

Two views:
  * Department / Supervisor / Team Lead view  -> all shots in a department.
  * Artist Portal                             -> shots assigned to the artist.

Implements the workflow features:
  (i) Assigning a shot to an artist auto-sets the artist status to "In Progress".
  (v) Submitting a shot for QC notifies the department supervisors & team leads.
"""

from flask import Blueprint, request

from auth.middleware import token_required
from common.constants import (
    ARTIST_STATUSES,
    BROAD_ACCESS_ROLES,
    DEPARTMENTS,
    SUPERVISOR_STATUSES,
)
from common.db_utils import create_notification, get_user, run_query
from common.http import failure, success
from common.serializers import SHOT_SELECT, shot_to_json

tasks_bp = Blueprint("tasks", __name__)


def _can_access(user, department):
    return user and (user["role"] in BROAD_ACCESS_ROLES or user["department"] == department)


def _notify_department_supervisors(department, message, notif_type):
    supervisors = run_query(
        """
        SELECT user_id FROM users
        WHERE department = %s AND role IN ('Supervisor', 'Team Lead') AND status = 'Active'
        """,
        (department,),
        fetch_all=True,
    ) or []
    for sup in supervisors:
        create_notification(message, notif_type, sup["user_id"])


@tasks_bp.route("/department", methods=["GET"])
@token_required
def department_view(current_user_id):
    """Supervisor / Team Lead view of all shots in a department.

    Broad-access roles (Admin / Production / Management) may view every pipeline
    department; when no department filter is supplied they get shots from all of
    them. Other roles are restricted to their own department.
    """
    user = get_user(current_user_id)
    if not user:
        return failure("User not found", 404)

    department = request.args.get("department")
    broad = user["role"] in BROAD_ACCESS_ROLES

    if department:
        if not _can_access(user, department):
            return failure("You are not allowed to access this department.", 403)
        rows = run_query(
            SHOT_SELECT + " WHERE s.department = %s ORDER BY s.allocated_date DESC, s.shot_code",
            (department,),
            fetch_all=True,
        ) or []
        return success({"shots": [shot_to_json(r) for r in rows]})

    if broad:
        placeholders = ", ".join(["%s"] * len(DEPARTMENTS))
        rows = run_query(
            SHOT_SELECT
            + f" WHERE s.department IN ({placeholders}) ORDER BY s.allocated_date DESC, s.shot_code",
            tuple(DEPARTMENTS),
            fetch_all=True,
        ) or []
        return success({"shots": [shot_to_json(r) for r in rows]})

    if user["department"] not in DEPARTMENTS:
        return failure("You do not have access to any pipeline department.", 403)

    rows = run_query(
        SHOT_SELECT + " WHERE s.department = %s ORDER BY s.allocated_date DESC, s.shot_code",
        (user["department"],),
        fetch_all=True,
    ) or []
    return success({"shots": [shot_to_json(r) for r in rows]})


@tasks_bp.route("/artist", methods=["GET"])
@token_required
def artist_portal(current_user_id):
    """Shots assigned to the currently logged-in artist."""
    rows = run_query(
        SHOT_SELECT + " WHERE s.artist_id = %s ORDER BY s.allocated_date DESC, s.shot_code",
        (current_user_id,),
        fetch_all=True,
    ) or []
    return success({"shots": [shot_to_json(r) for r in rows]})


@tasks_bp.route("/shots/<shot_id>/assign", methods=["PATCH"])
@token_required
def assign_shot(current_user_id, shot_id):
    shot = run_query(
        "SELECT shot_id, department, shot_code FROM shots WHERE shot_id = %s",
        (shot_id,),
        fetch_one=True,
    )
    if not shot:
        return failure("Shot not found", 404)
    if not _can_access(get_user(current_user_id), shot["department"]):
        return failure("You are not allowed to assign shots in this department.", 403)

    data = request.get_json(silent=True) or {}
    artist_id = (data.get("artistId") or "").strip()
    if not artist_id:
        return failure("artistId is required.", 400)

    artist = get_user(artist_id)
    if not artist:
        return failure("Artist not found", 404)

    # Feature (i): assigning auto-sets the artist status to "In Progress".
    run_query(
        """
        UPDATE shots
        SET artist_id = %s,
            artist_bid = %s,
            artist_eta = %s,
            allocated_date = COALESCE(%s, CURDATE()),
            artist_status = 'In Progress'
        WHERE shot_id = %s
        """,
        (
            artist_id,
            data.get("artistBid") or 0,
            data.get("artistEta"),
            data.get("allocatedDate"),
            shot_id,
        ),
    )
    create_notification(
        f"New shot {shot['shot_code']} has been assigned to you.",
        "Task Assigned",
        artist_id,
    )
    row = run_query(SHOT_SELECT + " WHERE s.shot_id = %s", (shot_id,), fetch_one=True)
    return success({"shot": shot_to_json(row)})


@tasks_bp.route("/shots/<shot_id>/artist-status", methods=["PATCH"])
@token_required
def update_artist_status(current_user_id, shot_id):
    shot = run_query(
        "SELECT shot_id, department, shot_code, artist_id FROM shots WHERE shot_id = %s",
        (shot_id,),
        fetch_one=True,
    )
    if not shot:
        return failure("Shot not found", 404)

    user = get_user(current_user_id)
    # Artists can only update their own shot; supervisors can update within dept.
    is_owner = shot["artist_id"] == current_user_id
    if not (is_owner or _can_access(user, shot["department"])):
        return failure("You are not allowed to update this shot.", 403)

    data = request.get_json(silent=True) or {}
    artist_status = data.get("artistStatus")
    if artist_status not in ARTIST_STATUSES:
        return failure("Invalid artist status.", 400)

    sets = ["artist_status = %s"]
    params = [artist_status]
    if "mandays" in data:
        sets.append("mandays = %s")
        params.append(data["mandays"])

    # Feature (v): submitting for QC notifies the supervisors & team leads.
    submitted_for_qc = artist_status in ("WIP Completed", "QC")

    params.append(shot_id)
    run_query(f"UPDATE shots SET {', '.join(sets)} WHERE shot_id = %s", tuple(params))

    if submitted_for_qc:
        sender = user["name"] if user else "An artist"
        _notify_department_supervisors(
            shot["department"],
            f"Shot {shot['shot_code']} submitted for QC by {sender}.",
            "QC Submitted",
        )

    row = run_query(SHOT_SELECT + " WHERE s.shot_id = %s", (shot_id,), fetch_one=True)
    return success({"shot": shot_to_json(row)})


@tasks_bp.route("/shots/<shot_id>/supervisor-status", methods=["PATCH"])
@token_required
def update_supervisor_status(current_user_id, shot_id):
    shot = run_query(
        "SELECT shot_id, department, shot_code, artist_id FROM shots WHERE shot_id = %s",
        (shot_id,),
        fetch_one=True,
    )
    if not shot:
        return failure("Shot not found", 404)
    if not _can_access(get_user(current_user_id), shot["department"]):
        return failure("You are not allowed to review shots in this department.", 403)

    data = request.get_json(silent=True) or {}
    supervisor_status = data.get("supervisorStatus")
    if supervisor_status not in SUPERVISOR_STATUSES:
        return failure("Invalid supervisor status.", 400)

    sets = ["supervisor_status = %s"]
    params = [supervisor_status]
    if "clientFeedback" in data:
        sets.append("client_feedback = %s")
        params.append(data["clientFeedback"])
    params.append(shot_id)
    run_query(f"UPDATE shots SET {', '.join(sets)} WHERE shot_id = %s", tuple(params))

    # Notify the assigned artist of feedback / approval.
    if shot["artist_id"]:
        create_notification(
            f"Supervisor updated shot {shot['shot_code']} to '{supervisor_status}'.",
            "Feedback",
            shot["artist_id"],
        )

    row = run_query(SHOT_SELECT + " WHERE s.shot_id = %s", (shot_id,), fetch_one=True)
    return success({"shot": shot_to_json(row)})
