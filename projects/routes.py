"""Projects routes.

Navigation: Department -> Client -> Show -> Shots.
Includes role-based access control: Supervisors / Team Leads / Artists are
restricted to their own department, while Admin / Production / Management have
broad access to every department.
"""

from flask import Blueprint, request

from auth.middleware import token_required
from common.constants import (
    ARTIST_STATUSES,
    BROAD_ACCESS_ROLES,
    DEPARTMENTS,
    SHOT_STATUSES,
    SUPERVISOR_STATUSES,
)
from common.db_utils import generate_prefixed_id, get_user, run_query
from common.http import failure, success
from common.serializers import SHOT_SELECT, shot_to_json

projects_bp = Blueprint("projects", __name__)


def _accessible_departments(user):
    """Return the list of departments a user is allowed to see."""
    if not user:
        return []
    if user["role"] in BROAD_ACCESS_ROLES:
        return DEPARTMENTS
    return [user["department"]] if user["department"] in DEPARTMENTS else []


def _can_access(user, department):
    return user and (user["role"] in BROAD_ACCESS_ROLES or user["department"] == department)


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@projects_bp.route("/departments", methods=["GET"])
@token_required
def list_departments(current_user_id):
    user = get_user(current_user_id)
    return success({"departments": _accessible_departments(user)})


@projects_bp.route("/clients", methods=["GET"])
@token_required
def list_clients(_current_user_id):
    rows = run_query(
        "SELECT client_id, client_name FROM clients ORDER BY client_id",
        fetch_all=True,
    ) or []
    clients = [{"clientId": r["client_id"], "clientName": r["client_name"]} for r in rows]
    return success({"clients": clients})


@projects_bp.route("/clients", methods=["POST"])
@token_required
def create_client(current_user_id):
    """Create a new client. Restricted to broad-access roles."""
    user = get_user(current_user_id)
    if not user or user["role"] not in BROAD_ACCESS_ROLES:
        return failure("You are not allowed to create clients.", 403)

    data = request.get_json(silent=True) or {}
    client_name = (data.get("clientName") or "").strip()
    if not client_name:
        return failure("clientName is required.", 400)

    client_id = generate_prefixed_id("clients", "client_id", "CLT", 0)
    run_query(
        "INSERT INTO clients (client_id, client_name) VALUES (%s, %s)",
        (client_id, client_name),
    )
    return success({"client": {"clientId": client_id, "clientName": client_name}}, 201)


@projects_bp.route("/clients/<client_id>/shows", methods=["GET"])
@token_required
def shows_for_client(_current_user_id, client_id):
    rows = run_query(
        "SELECT show_id, client_id, show_name FROM shows WHERE client_id = %s ORDER BY show_name",
        (client_id,),
        fetch_all=True,
    ) or []
    shows = [
        {"showId": r["show_id"], "clientId": r["client_id"], "showName": r["show_name"]}
        for r in rows
    ]
    return success({"shows": shows})


@projects_bp.route("/clients/<client_id>/shows", methods=["POST"])
@token_required
def create_show(current_user_id, client_id):
    """Create a new show under a client. Restricted to broad-access roles."""
    user = get_user(current_user_id)
    if not user or user["role"] not in BROAD_ACCESS_ROLES:
        return failure("You are not allowed to create shows.", 403)

    client = run_query(
        "SELECT client_id FROM clients WHERE client_id = %s", (client_id,), fetch_one=True
    )
    if not client:
        return failure("Client not found", 404)

    data = request.get_json(silent=True) or {}
    show_name = (data.get("showName") or "").strip()
    if not show_name:
        return failure("showName is required.", 400)

    show_id = generate_prefixed_id("shows", "show_id", "SHW", 0)
    run_query(
        "INSERT INTO shows (show_id, client_id, show_name) VALUES (%s, %s, %s)",
        (show_id, client_id, show_name),
    )
    return success(
        {"show": {"showId": show_id, "clientId": client_id, "showName": show_name}}, 201
    )


@projects_bp.route("/shots", methods=["GET"])
@token_required
def list_shots(current_user_id):
    """List shots filtered by department / client / show / status with access control."""
    user = get_user(current_user_id)
    department = request.args.get("department")
    client_id = request.args.get("clientId")
    show_id = request.args.get("showId")
    status = request.args.get("status")

    allowed = _accessible_departments(user)
    if not allowed:
        return failure("You do not have access to any department.", 403)

    clauses = []
    params = []

    if department:
        if department not in allowed:
            return failure("You are not allowed to access this department.", 403)
        clauses.append("s.department = %s")
        params.append(department)
    else:
        placeholders = ", ".join(["%s"] * len(allowed))
        clauses.append(f"s.department IN ({placeholders})")
        params.extend(allowed)

    if client_id:
        clauses.append("sh.client_id = %s")
        params.append(client_id)
    if show_id:
        clauses.append("s.show_id = %s")
        params.append(show_id)
    if status:
        clauses.append("s.status = %s")
        params.append(status)

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = run_query(SHOT_SELECT + where + " ORDER BY s.shot_code", tuple(params), fetch_all=True) or []
    return success({"shots": [shot_to_json(r) for r in rows]})


@projects_bp.route("/shots/<shot_id>", methods=["GET"])
@token_required
def get_shot(current_user_id, shot_id):
    row = run_query(SHOT_SELECT + " WHERE s.shot_id = %s", (shot_id,), fetch_one=True)
    if not row:
        return failure("Shot not found", 404)
    if not _can_access(get_user(current_user_id), row["department"]):
        return failure("You are not allowed to access this department.", 403)
    return success({"shot": shot_to_json(row)})


@projects_bp.route("/shots", methods=["POST"])
@token_required
def create_shot(current_user_id):
    user = get_user(current_user_id)
    data = request.get_json(silent=True) or {}

    show_id = (data.get("showId") or "").strip()
    department = (data.get("department") or "").strip()
    shot_code = (data.get("shotCode") or "").strip()

    if not all([show_id, department, shot_code]):
        return failure("showId, department and shotCode are required.", 400)
    if department not in DEPARTMENTS:
        return failure("Invalid department.", 400)
    if not _can_access(user, department):
        return failure("You are not allowed to create shots in this department.", 403)

    show = run_query("SELECT show_id FROM shows WHERE show_id = %s", (show_id,), fetch_one=True)
    if not show:
        return failure("Show not found", 404)

    status = data.get("status") or "Awaiting Approval"
    if status not in SHOT_STATUSES:
        return failure("Invalid status.", 400)

    shot_id = generate_prefixed_id("shots", "shot_id", "SHT", 0)
    run_query(
        """
        INSERT INTO shots
            (shot_id, show_id, department, shot_code, frame_in, frame_out,
             supervisor_bid, client_bid, client_eta, notes, status, description, due_date, client_feedback)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            shot_id,
            show_id,
            department,
            shot_code,
            data.get("frameIn") or 0,
            data.get("frameOut") or 0,
            data.get("supervisorBid") or 0,
            data.get("clientBid") or 0,
            data.get("clientEta"),
            data.get("notes"),
            status,
            data.get("description"),
            data.get("dueDate"),
            data.get("clientFeedback"),
        ),
    )
    row = run_query(SHOT_SELECT + " WHERE s.shot_id = %s", (shot_id,), fetch_one=True)
    return success({"shot": shot_to_json(row)}, 201)


@projects_bp.route("/shots/<shot_id>", methods=["PUT"])
@token_required
def update_shot(current_user_id, shot_id):
    existing = run_query("SELECT department FROM shots WHERE shot_id = %s", (shot_id,), fetch_one=True)
    if not existing:
        return failure("Shot not found", 404)
    if not _can_access(get_user(current_user_id), existing["department"]):
        return failure("You are not allowed to modify this department.", 403)

    data = request.get_json(silent=True) or {}
    field_map = {
        "shotCode": "shot_code",
        "frameIn": "frame_in",
        "frameOut": "frame_out",
        "supervisorBid": "supervisor_bid",
        "clientBid": "client_bid",
        "clientEta": "client_eta",
        "notes": "notes",
        "status": "status",
        "description": "description",
        "dueDate": "due_date",
        "clientFeedback": "client_feedback",
    }
    sets = []
    params = []
    for json_key, column in field_map.items():
        if json_key in data:
            if json_key == "status" and data[json_key] not in SHOT_STATUSES:
                return failure("Invalid status.", 400)
            sets.append(f"{column} = %s")
            params.append(data[json_key])

    if not sets:
        return failure("No fields to update.", 400)

    params.append(shot_id)
    run_query(f"UPDATE shots SET {', '.join(sets)} WHERE shot_id = %s", tuple(params))
    row = run_query(SHOT_SELECT + " WHERE s.shot_id = %s", (shot_id,), fetch_one=True)
    return success({"shot": shot_to_json(row)})


@projects_bp.route("/shots/<shot_id>/status", methods=["PATCH"])
@token_required
def update_shot_status(current_user_id, shot_id):
    existing = run_query("SELECT department FROM shots WHERE shot_id = %s", (shot_id,), fetch_one=True)
    if not existing:
        return failure("Shot not found", 404)
    if not _can_access(get_user(current_user_id), existing["department"]):
        return failure("You are not allowed to modify this department.", 403)

    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in SHOT_STATUSES:
        return failure("Invalid status.", 400)

    run_query("UPDATE shots SET status = %s WHERE shot_id = %s", (status, shot_id))
    row = run_query(SHOT_SELECT + " WHERE s.shot_id = %s", (shot_id,), fetch_one=True)
    return success({"shot": shot_to_json(row)})


@projects_bp.route("/shots/<shot_id>", methods=["DELETE"])
@token_required
def delete_shot(current_user_id, shot_id):
    existing = run_query("SELECT department FROM shots WHERE shot_id = %s", (shot_id,), fetch_one=True)
    if not existing:
        return failure("Shot not found", 404)
    if not _can_access(get_user(current_user_id), existing["department"]):
        return failure("You are not allowed to modify this department.", 403)

    run_query("DELETE FROM shots WHERE shot_id = %s", (shot_id,))
    return success({"message": "Shot deleted", "shotId": shot_id})


@projects_bp.route("/shots/bulk-upsert", methods=["POST"])
@token_required
def bulk_upsert_shots(current_user_id):
    """Create or update shots from imported rows (used by Excel import flow)."""
    user = get_user(current_user_id)
    data = request.get_json(silent=True) or {}
    rows = data.get("rows") or []
    if not isinstance(rows, list) or not rows:
        return failure("rows is required and must be a non-empty array.", 400)

    created = 0
    updated = 0
    errors = []

    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            errors.append(f"Row {idx}: invalid row format.")
            continue

        show_id = (row.get("showId") or "").strip()
        department = (row.get("department") or "").strip()
        shot_code = (row.get("shotCode") or "").strip()

        if not show_id or not department or not shot_code:
            errors.append(
                f"Row {idx}: showId, department and shotCode are required."
            )
            continue
        if department not in DEPARTMENTS:
            errors.append(f"Row {idx}: invalid department '{department}'.")
            continue
        if not _can_access(user, department):
            errors.append(
                f"Row {idx}: you are not allowed to modify department '{department}'."
            )
            continue

        if not run_query(
            "SELECT show_id FROM shows WHERE show_id = %s", (show_id,), fetch_one=True
        ):
            errors.append(f"Row {idx}: show '{show_id}' not found.")
            continue

        status = row.get("status") or "Awaiting Approval"
        if status not in SHOT_STATUSES:
            errors.append(f"Row {idx}: invalid status '{status}'.")
            continue

        artist_status = row.get("artistStatus") or "YTS"
        if artist_status not in ARTIST_STATUSES:
            errors.append(f"Row {idx}: invalid artistStatus '{artist_status}'.")
            continue

        supervisor_status = row.get("supervisorStatus")
        if supervisor_status is not None and supervisor_status not in SUPERVISOR_STATUSES:
            errors.append(
                f"Row {idx}: invalid supervisorStatus '{supervisor_status}'."
            )
            continue

        existing = run_query(
            """
            SELECT shot_id
            FROM shots
            WHERE show_id = %s AND department = %s AND shot_code = %s
            LIMIT 1
            """,
            (show_id, department, shot_code),
            fetch_one=True,
        )

        if existing:
            run_query(
                """
                UPDATE shots
                SET frame_in = %s,
                    frame_out = %s,
                    supervisor_bid = %s,
                    client_bid = %s,
                    client_eta = %s,
                    notes = %s,
                    status = %s,
                    description = %s,
                    due_date = %s,
                    supervisor_status = %s,
                    artist_status = %s,
                    artist_bid = %s,
                    artist_eta = %s,
                    mandays = %s,
                    allocated_date = %s,
                    client_feedback = %s,
                    artist_id = %s
                WHERE shot_id = %s
                """,
                (
                    _to_int(row.get("frameIn")),
                    _to_int(row.get("frameOut")),
                    _to_float(row.get("supervisorBid")),
                    _to_float(row.get("clientBid")),
                    row.get("clientEta"),
                    row.get("notes"),
                    status,
                    row.get("description"),
                    row.get("dueDate"),
                    supervisor_status,
                    artist_status,
                    _to_float(row.get("artistBid")),
                    row.get("artistEta"),
                    _to_float(row.get("mandays")),
                    row.get("allocatedDate"),
                    row.get("clientFeedback"),
                    row.get("artistId"),
                    existing["shot_id"],
                ),
            )
            updated += 1
        else:
            shot_id = generate_prefixed_id("shots", "shot_id", "SHT", 0)
            run_query(
                """
                INSERT INTO shots
                    (shot_id, show_id, department, shot_code, frame_in, frame_out,
                     supervisor_bid, client_bid, client_eta, notes, status, description,
                     due_date, supervisor_status, artist_status, artist_bid, artist_eta,
                     mandays, allocated_date, client_feedback, artist_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    shot_id,
                    show_id,
                    department,
                    shot_code,
                    _to_int(row.get("frameIn")),
                    _to_int(row.get("frameOut")),
                    _to_float(row.get("supervisorBid")),
                    _to_float(row.get("clientBid")),
                    row.get("clientEta"),
                    row.get("notes"),
                    status,
                    row.get("description"),
                    row.get("dueDate"),
                    supervisor_status,
                    artist_status,
                    _to_float(row.get("artistBid")),
                    row.get("artistEta"),
                    _to_float(row.get("mandays")),
                    row.get("allocatedDate"),
                    row.get("clientFeedback"),
                    row.get("artistId"),
                ),
            )
            created += 1

    return success(
        {
            "created": created,
            "updated": updated,
            "errors": errors,
        }
    )
