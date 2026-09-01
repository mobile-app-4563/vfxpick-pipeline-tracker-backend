"""Projects routes.

Navigation: Department -> Client -> Show -> Shots.
Includes role-based access control: Supervisors / Team Leads / Artists are
restricted to their own department, while Admin / Production / Management have
broad access to every department.
"""

import re
import secrets
from datetime import datetime

import bcrypt
from flask import Blueprint, request

from auth.middleware import token_required
from access.routes import delete_enabled_for_user, menu_granted_for_user
from common.audit import write_activity_log
from common.constants import (
    ARTIST_STATUSES,
    BROAD_ACCESS_ROLES,
    SHOT_STATUSES,
    SUPERVISOR_STATUSES,
)
from common.db_utils import generate_prefixed_id, get_user, run_query, to_sql_date
from common.http import failure, success
from common.options_store import effective_pipeline_departments
from common.serializers import SHOT_SELECT, shot_to_json
from database.connection import get_db

projects_bp = Blueprint("projects", __name__)


def _accessible_departments(user):
    """Return the list of departments a user is allowed to see."""
    if not user:
        return []
    if user["role"] in BROAD_ACCESS_ROLES:
        return effective_pipeline_departments()
    # A user granted the Projects menu in the Access Provider matrix may
    # browse every department, same as broad-access roles.
    if menu_granted_for_user(user, "/projects"):
        return effective_pipeline_departments()
    # Runtime-added departments are tracked in the options store; a
    # non-broad user sees their own department even if it was added at
    # runtime (so it must also pass the const check for backwards
    # compatibility, but new depts are still valid via the options store).
    if user["department"] in effective_pipeline_departments():
        return [user["department"]]
    return []


def _split_departments(department):
    """Normalize a (possibly comma-separated) department string to a list."""
    if not department:
        return []
    return [d.strip() for d in str(department).split(",") if d.strip()]


def _can_access(user, department):
    if not user:
        return False
    if user["role"] in BROAD_ACCESS_ROLES:
        return True
    # department may be a comma-separated list (multi-department shot).
    return user["department"] in _split_departments(department)


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


# Tokens that mean "no artist assigned" — never resolve or auto-create a user.
_EMPTY_ARTIST_TOKENS = {
    "", "-", "--", "n/a", "na", "none", "null", "nil", "0",
    "unassigned", "not assigned", "not_assigned", "notassigned",
    "tbd", "todo", "pending",
}


def _slugify_artist_name(text):
    """Turn a display name into a lowercase, dot-separated slug."""
    slug = re.sub(r"[^a-z0-9]+", ".", text.lower()).strip(".")
    return slug or "artist"


def _next_user_id(cursor):
    """Return the next free USR-prefixed user id (MAX-based, collision-safe)."""
    cursor.execute(
        "SELECT MAX(CAST(SUBSTRING(user_id, 4) AS UNSIGNED)) AS max_num "
        "FROM users WHERE user_id LIKE 'USR%'"
    )
    max_num = cursor.fetchone()["max_num"] or 0
    candidate = max(max_num, 100) + 1
    while True:
        uid = f"USR{candidate}"
        cursor.execute("SELECT user_id FROM users WHERE user_id = %s", (uid,))
        if not cursor.fetchone():
            return uid
        candidate += 1


def _create_import_user(cursor, name, department):
    """Create a placeholder artist user so the imported name is persisted.

    The user gets a random (unusable) password and a unique @import.local
    email, so the name always survives on the shot and can be re-linked to a
    real account later.  Runs inside the import transaction — if the batch is
    rolled back the placeholder is rolled back with it.
    """
    user_id = _next_user_id(cursor)
    slug = _slugify_artist_name(name)
    email = f"{slug}@import.local"
    suffix = 1
    while True:
        cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
        if not cursor.fetchone():
            break
        suffix += 1
        email = f"{slug}{suffix}@import.local"
    password_hash = bcrypt.hashpw(
        secrets.token_urlsafe(24).encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")
    avatar = "".join(part[0].upper() for part in name.split() if part)[:2] or "A"
    cursor.execute(
        """
        INSERT INTO users
            (user_id, name, email, department, password_hash, role, status, avatar)
        VALUES (%s, %s, %s, %s, %s, 'Artist', 'Active', %s)
        """,
        (user_id, name, email, department, password_hash, avatar),
    )
    try:
        cursor.execute("INSERT INTO user_settings (user_id) VALUES (%s)", (user_id,))
    except Exception:
        # user_settings is optional; never let a missing table fail the batch.
        pass
    return user_id


def _lookup_user_id(cursor, cache, key, query, params):
    """Memoized single-row user_id lookup; returns None when unmatched."""
    if key in cache:
        return cache[key]
    cursor.execute(query, params)
    row = cursor.fetchone()
    resolved = row["user_id"] if row else None
    cache[key] = resolved
    return resolved


def _resolve_artist_id(cursor, artist_id, artist_name, department, cache):
    """Resolve an imported artist to a users.user_id.

    Match order:
      1. explicit artistId (preferred),
      2. exact users.name (case-insensitive, whitespace-normalized),
      3. email local-part (john.doe vs john.doe@studio.com),
      4. employee_id_ext,
      5. first name, unique within the shot's department,
      6. auto-create a placeholder Artist user so the name is always kept.

    Returns (user_id_or_None, note_or_None).  Notes are informational only
    (e.g. "created new user ..."); they never fail the batch.
    """
    raw_id = (artist_id or "").strip() if artist_id else ""
    name = (artist_name or "").strip() if artist_name else ""
    normalized = " ".join(name.split()) if name else ""
    if normalized.lower() in _EMPTY_ARTIST_TOKENS:
        normalized = ""

    # 1) Explicit artistId (fall through to name matching when it fails)
    if raw_id:
        key = ("id", raw_id.lower())
        resolved = _lookup_user_id(
            cursor,
            cache,
            key,
            "SELECT user_id FROM users WHERE user_id = %s LIMIT 1",
            (raw_id,),
        )
        if resolved:
            return resolved, None
        if not normalized:
            return None, None

    if not normalized:
        return None, None
    key = ("name", normalized.lower())
    if key in cache:
        return cache[key], None

    # 2) Exact name (case-insensitive, whitespace-normalized)
    resolved = _lookup_user_id(
        cursor,
        cache,
        key,
        "SELECT user_id FROM users WHERE LOWER(TRIM(name)) = %s LIMIT 1",
        (normalized.lower(),),
    )
    if resolved:
        return resolved, None

    # 3) Email local-part
    resolved = _lookup_user_id(
        cursor,
        cache,
        ("email", normalized.lower()),
        "SELECT user_id FROM users "
        "WHERE LOWER(SUBSTRING_INDEX(email, '@', 1)) = %s LIMIT 1",
        (normalized.lower(),),
    )
    if resolved:
        cache[key] = resolved
        return resolved, None

    # 4) employee_id_ext
    resolved = _lookup_user_id(
        cursor,
        cache,
        ("emp", normalized.lower()),
        "SELECT user_id FROM users "
        "WHERE LOWER(TRIM(employee_id_ext)) = %s LIMIT 1",
        (normalized.lower(),),
    )
    if resolved:
        cache[key] = resolved
        return resolved, None

    # 5) First name, unique within the shot's department
    first = normalized.split(" ", 1)[0].lower()
    if first and first != normalized.lower():
        cursor.execute(
            "SELECT user_id FROM users "
            "WHERE LOWER(TRIM(SUBSTRING_INDEX(name, ' ', 1))) = %s "
            "AND department = %s LIMIT 2",
            (first, department),
        )
        rows = cursor.fetchall()
        if len(rows) == 1:
            cache[key] = rows[0]["user_id"]
            return rows[0]["user_id"], None

    # 6) Last resort: create a placeholder user so the name is persisted
    new_id = _create_import_user(cursor, normalized[:95], department)
    cache[key] = new_id
    return new_id, f"artist '{normalized}' not found — created new user ({new_id})"


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
    if not user or (
        user["role"] not in BROAD_ACCESS_ROLES
        and not menu_granted_for_user(user, "/projects")
    ):
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
    write_activity_log(
        current_user_id,
        "Projects",
        "CREATE",
        "Client",
        client_id,
        {"clientName": client_name},
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
    if not user or (
        user["role"] not in BROAD_ACCESS_ROLES
        and not menu_granted_for_user(user, "/projects")
    ):
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
    write_activity_log(
        current_user_id,
        "Projects",
        "CREATE",
        "Show",
        show_id,
        {"clientId": client_id, "showName": show_name},
    )
    return success(
        {"show": {"showId": show_id, "clientId": client_id, "showName": show_name}}, 201
    )


@projects_bp.route("/shots", methods=["GET"])
@token_required
def list_shots(current_user_id):
    """List shots filtered by department / client / show / status with pagination."""
    user = get_user(current_user_id)
    department = request.args.get("department")
    client_id = request.args.get("clientId")
    show_id = request.args.get("showId")
    status = request.args.get("status")
    limit = request.args.get("limit", type=int, default=500)
    offset = request.args.get("offset", type=int, default=0)

    # Clamp limit to avoid OOM
    limit = min(limit, 2000)

    allowed = _accessible_departments(user)
    if not allowed:
        return failure("You do not have access to any department.", 403)

    clauses = []
    params = []

    if department:
        requested = _split_departments(department)
        if not requested or any(d not in allowed for d in requested):
            return failure("You are not allowed to access this department.", 403)
        # Shots can carry multiple comma-separated departments; match any.
        clauses.append(
            "(" + " OR ".join(["FIND_IN_SET(%s, s.department)"] * len(requested)) + ")"
        )
        params.extend(requested)
    else:
        clauses.append(
            "(" + " OR ".join(["FIND_IN_SET(%s, s.department)"] * len(allowed)) + ")"
        )
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

    # Count total matching rows
    count_row = run_query(
        "SELECT COUNT(*) AS total FROM shots s JOIN shows sh ON s.show_id = sh.show_id" + where,
        tuple(params),
        fetch_one=True,
    )
    total = count_row["total"] if count_row else 0

    # Fetch paginated rows
    rows = run_query(
        SHOT_SELECT + where + " ORDER BY s.shot_code LIMIT %s OFFSET %s",
        tuple(list(params) + [limit, offset]),
        fetch_all=True,
    ) or []

    return success({
        "shots": [shot_to_json(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    })


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
    dept_parts = _split_departments(department)
    department = ",".join(dept_parts)
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
             total_frames, supervisor_bid, client_bid, client_eta, notes, status,
             description, due_date, client_feedback,
             coordinator, level_of_shot, allocation_date, allocation_eta,
             starting_date, complete_date, daily_wip,
             consumed_mandays, saved_mandays, approved_version, approved_by,
             comments, complexity, priority, from_roto, from_paint, from_mm, from_comp)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            shot_id,
            show_id,
            department,
            shot_code,
            data.get("frameIn") or 0,
            data.get("frameOut") or 0,
            data.get("totalFrames") or 0,
            data.get("supervisorBid") or 0,
            data.get("clientBid") or 0,
            to_sql_date(data.get("clientEta")),
            data.get("notes"),
            status,
            data.get("description"),
            to_sql_date(data.get("dueDate") or data.get("clientEta")),
            data.get("clientFeedback"),
            data.get("coordinator"),
            data.get("levelOfShot"),
            to_sql_date(data.get("allocationDate")),
            to_sql_date(data.get("allocationEta")),
            to_sql_date(data.get("startingDate")),
            to_sql_date(data.get("completeDate")),
            data.get("dailyWip") or 0,
            data.get("consumedMandays") or 0,
            data.get("savedMandays") or 0,
            data.get("approvedVersion"),
            data.get("approvedBy"),
            data.get("comments"),
            data.get("complexity"),
            data.get("priority"),
            data.get("fromRoto"),
            data.get("fromPaint"),
            data.get("fromMm"),
            data.get("fromComp"),
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
        "totalFrames": "total_frames",
        "supervisorBid": "supervisor_bid",
        "clientBid": "client_bid",
        "clientEta": "client_eta",
        "notes": "notes",
        "status": "status",
        "description": "description",
        "dueDate": "due_date",
        "clientFeedback": "client_feedback",
        "coordinator": "coordinator",
        "levelOfShot": "level_of_shot",
        "allocationDate": "allocation_date",
        "allocationEta": "allocation_eta",
        "startingDate": "starting_date",
        "completeDate": "complete_date",
        "dailyWip": "daily_wip",
        "mandays": "mandays",
        "consumedMandays": "consumed_mandays",
        "savedMandays": "saved_mandays",
        "approvedVersion": "approved_version",
        "approvedBy": "approved_by",
        "comments": "comments",
        "complexity": "complexity",
        "priority": "priority",
        "fromRoto": "from_roto",
        "fromPaint": "from_paint",
        "fromMm": "from_mm",
        "fromComp": "from_comp",
    }
    sets = []
    params = []
    date_fields = {
        "clientEta",
        "dueDate",
        "allocationDate",
        "allocationEta",
        "startingDate",
        "completeDate",
        "artistEta",
        "allocatedDate",
    }
    for json_key, column in field_map.items():
        if json_key in data:
            if json_key == "status" and data[json_key] not in SHOT_STATUSES:
                return failure("Invalid status.", 400)
            sets.append(f"{column} = %s")
            params.append(
                to_sql_date(data[json_key]) if json_key in date_fields else data[json_key]
            )

    if not sets:
        return failure("No fields to update.", 400)

    params.append(shot_id)
    run_query(f"UPDATE shots SET {', '.join(sets)} WHERE shot_id = %s", tuple(params))
    write_activity_log(
        current_user_id,
        "Projects",
        "UPDATE",
        "Shot",
        shot_id,
        {"fields": list(data.keys()), "values": data},
    )
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

    previous_status = run_query(
        "SELECT status FROM shots WHERE shot_id = %s", (shot_id,), fetch_one=True
    )
    run_query("UPDATE shots SET status = %s WHERE shot_id = %s", (status, shot_id))
    write_activity_log(
        current_user_id,
        "Projects",
        "STATUS_UPDATE",
        "Shot",
        shot_id,
        {"oldStatus": previous_status.get("status") if previous_status else None, "newStatus": status},
    )
    row = run_query(SHOT_SELECT + " WHERE s.shot_id = %s", (shot_id,), fetch_one=True)
    return success({"shot": shot_to_json(row)})


@projects_bp.route("/shots/<shot_id>", methods=["DELETE"])
@token_required
def delete_shot(current_user_id, shot_id):
    existing = run_query("SELECT department FROM shots WHERE shot_id = %s", (shot_id,), fetch_one=True)
    if not existing:
        return failure("Shot not found", 404)
    user = get_user(current_user_id)
    if not _can_access(user, existing["department"]):
        return failure("You are not allowed to modify this department.", 403)
    if not delete_enabled_for_user(user):
        return failure(
            "Access denied: delete is disabled for your department.", 403
        )

    run_query("DELETE FROM shots WHERE shot_id = %s", (shot_id,))
    write_activity_log(
        current_user_id,
        "Projects",
        "DELETE",
        "Shot",
        shot_id,
        {"department": existing["department"]},
    )
    return success({"message": "Shot deleted", "shotId": shot_id})


@projects_bp.route("/shots/bulk-delete", methods=["POST"])
@token_required
def bulk_delete_shots(current_user_id):
    """Delete multiple shots in a single request.
    
    Expects JSON: {"shotIds": ["SH001", "SH002", ...]}
    Deletes are performed in a single transaction.  Returns counts of
    deleted and skipped (not-found / access-denied) shot IDs.
    """
    body = request.get_json(silent=True) or {}
    shot_ids = body.get("shotIds", [])
    if not shot_ids or not isinstance(shot_ids, list):
        return failure("shotIds (list) is required.", 400)

    user = get_user(current_user_id)
    if not delete_enabled_for_user(user):
        return failure(
            "Access denied: delete is disabled for your department.", 403
        )
    deleted = 0
    skipped = 0
    cnx = get_db()
    try:
        cnx.autocommit = False
        with cnx.cursor(buffered=True) as cur:
            for sid in shot_ids:
                cur.execute(
                    "SELECT department FROM shots WHERE shot_id = %s", (sid,)
                )
                row = cur.fetchone()
                if not row:
                    skipped += 1
                    continue
                if not _can_access(user, row[0]):
                    skipped += 1
                    continue
                cur.execute("DELETE FROM shots WHERE shot_id = %s", (sid,))
                deleted += 1
        cnx.commit()
    except Exception:
        cnx.rollback()
        raise
    finally:
        cnx.close()

    if deleted:
        write_activity_log(
            current_user_id,
            "Projects",
            "BULK_DELETE",
            "Shot",
            ",".join(shot_ids[:50]),
            {"requested": len(shot_ids), "deleted": deleted, "skipped": skipped},
        )

    return success({
        "message": f"Deleted {deleted} shot(s), skipped {skipped}.",
        "deleted": deleted,
        "skipped": skipped,
    })


@projects_bp.route("/shots/bulk-upsert", methods=["POST"])
@token_required
def bulk_upsert_shots(current_user_id):
    """Create or update shots from imported rows (used by Excel import flow).

    Runs inside a single database transaction so that either all rows succeed
    together or all are rolled back.  Uses one connection for the entire
    operation to avoid pool exhaustion.
    """
    data = request.get_json(silent=True) or {}
    rows = data.get("rows") or []
    if not isinstance(rows, list) or not rows:
        return failure("rows is required and must be a non-empty array.", 400)
    valid_rows = []
    errors = []
    notes = []

    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            errors.append(f"Row {idx}: invalid row format.")
            continue

        show_id = (row.get("showId") or "").strip()
        department = (row.get("department") or "").strip()
        shot_code = (row.get("shotCode") or "").strip()

        if not show_id or not department or not shot_code:
            errors.append(f"Row {idx}: showId, department and shotCode are required.")
            continue
        dept_parts = _split_departments(department)
        # Normalize the stored value (trimmed, comma-joined, no spaces).
        department = ",".join(dept_parts)
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
            errors.append(f"Row {idx}: invalid supervisorStatus '{supervisor_status}'.")
            continue

        valid_rows.append((idx, show_id, department, shot_code, row, status, artist_status, supervisor_status))

    if not valid_rows:
        return success({"created": 0, "updated": 0, "errors": errors, "notes": notes})

    # ── Get ONE connection for the entire transaction ─────────────────────
    conn = get_db()
    conn.autocommit = False
    created = 0
    updated = 0

    try:
        cursor = conn.cursor(dictionary=True, buffered=True)

        # ── Validate shows once per unique show_id (not per row) ──────
        show_cache = set()
        for _, show_id, *_ in valid_rows:
            if show_id not in show_cache:
                cursor.execute("SELECT show_id FROM shows WHERE show_id = %s", (show_id,))
                if not cursor.fetchone():
                    errors.append(f"Show '{show_id}' not found.")
                    # Mark all rows for this show as invalid
                else:
                    show_cache.add(show_id)

        # ── Batch-fetch existing shots for these shows ──────────────────
        # Match by show_id only, then compare departments client-side so
        # comma-separated multi-department lists match regardless of order.
        existing_map = {}  # (show_id, frozenset(depts), shot_code) -> shot_id
        shows_to_fetch = {show_id for _, show_id, *_ in valid_rows if show_id in show_cache}
        for show_id in shows_to_fetch:
            cursor.execute(
                "SELECT shot_id, show_id, department, shot_code FROM shots "
                "WHERE show_id = %s",
                (show_id,),
            )
            for r in cursor.fetchall():
                dept_set = frozenset(_split_departments(r["department"]))
                existing_map[(r["show_id"], dept_set, r["shot_code"])] = r["shot_id"]

        # ── Update / Insert each row ────────────────────────────────────
        update_sql = """
            UPDATE shots
            SET frame_in = %s, frame_out = %s, total_frames = %s,
                supervisor_bid = %s, client_bid = %s, client_eta = %s,
                notes = %s, status = %s, description = %s, due_date = %s,
                supervisor_status = %s, artist_status = %s, artist_bid = %s,
                artist_eta = %s, mandays = %s, allocated_date = %s,
                client_feedback = %s, artist_id = %s, coordinator = %s,
                level_of_shot = %s, allocation_date = %s, allocation_eta = %s,
                starting_date = %s, complete_date = %s, daily_wip = %s,
                consumed_mandays = %s, saved_mandays = %s,
                approved_version = %s, approved_by = %s, comments = %s,
                complexity = %s, priority = %s, from_roto = %s, from_paint = %s,
                from_mm = %s, from_comp = %s
            WHERE shot_id = %s
        """

        insert_sql = """
            INSERT INTO shots
                (shot_id, show_id, department, shot_code, frame_in, frame_out,
                 total_frames, supervisor_bid, client_bid, client_eta, notes, status,
                 description, due_date, supervisor_status, artist_status, artist_bid,
                 artist_eta, mandays, allocated_date, client_feedback, artist_id,
                 coordinator, level_of_shot, allocation_date, allocation_eta,
                 starting_date, complete_date, daily_wip,
                 consumed_mandays, saved_mandays, approved_version, approved_by,
                 comments, complexity, priority, from_roto, from_paint, from_mm, from_comp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        upsert_params = _build_upsert_params()
        artist_cache = {}
        for idx, show_id, department, shot_code, row, status, artist_status, supervisor_status in valid_rows:
            if show_id not in show_cache:
                errors.append(f"Row {idx}: show '{show_id}' not found.")
                continue

            # Multi-department shots: use the first department for artist
            # resolution / placeholder creation (users have a single dept).
            primary_department = _split_departments(department)[0]
            artist_id, artist_note = _resolve_artist_id(
                cursor,
                row.get("artistId"),
                row.get("artistName"),
                primary_department,
                artist_cache,
            )
            artist_name_raw = (row.get("artistName") or "").strip()
            if artist_name_raw and not artist_id:
                errors.append(
                    f"Row {idx}: artist '{artist_name_raw}' was not found in "
                    f"users; shot imported without an artist."
                )
            elif artist_note:
                notes.append(f"Row {idx}: {artist_note}")

            existing_key = (show_id, frozenset(_split_departments(department)), shot_code)
            params = upsert_params(
                row,
                show_id,
                department,
                shot_code,
                status,
                artist_status,
                supervisor_status,
                artist_id,
            )

            if existing_key in existing_map:
                cursor.execute(update_sql, (*params, existing_map[existing_key]))
                updated += 1
            else:
                shot_id = generate_prefixed_id_cursor(cursor, "shots", "shot_id", "SHT", 0)
                cursor.execute(insert_sql, (shot_id, show_id, department, shot_code, *params))
                created += 1

        conn.commit()
    except Exception as exc:
        conn.rollback()
        return failure(f"Bulk upsert failed (rolled back): {str(exc)}", 500)
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    if created or updated:
        write_activity_log(
            current_user_id,
            "Projects",
            "BULK_UPSERT",
            "Shot",
            f"{created} created / {updated} updated",
            {"created": created, "updated": updated, "errors": len(errors)},
        )

    return success({"created": created, "updated": updated, "errors": errors, "notes": notes})


def generate_prefixed_id_cursor(cursor, table_name, id_column, prefix, start_number):
    """Generate an ID using an existing cursor (inside a transaction)."""
    cursor.execute(f"SELECT COUNT(*) AS cnt FROM {table_name}")
    row = cursor.fetchone()
    return f"{prefix}{start_number + row['cnt'] + 1}"


def _build_upsert_params():
    """Build the ordered param tuple for INSERT/UPDATE from a single import row.

    Returns a callable that takes the per-row values and returns a flat tuple.
    """
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

    def params(row, show_id, department, shot_code, status, artist_status, supervisor_status, artist_id):
        return (
            _to_int(row.get("frameIn")),
            _to_int(row.get("frameOut")),
            _to_int(row.get("totalFrames")),
            _to_float(row.get("supervisorBid")),
            _to_float(row.get("clientBid")),
            to_sql_date(row.get("clientEta")),
            row.get("notes"),
            status,
            row.get("description"),
            to_sql_date(row.get("dueDate") or row.get("clientEta")),
            supervisor_status,
            artist_status,
            _to_float(row.get("artistBid")),
            to_sql_date(row.get("artistEta")),
            _to_float(row.get("mandays")),
            to_sql_date(row.get("allocatedDate")),
            row.get("clientFeedback"),
            artist_id,
            row.get("coordinator"),
            row.get("levelOfShot"),
            to_sql_date(row.get("allocationDate")),
            to_sql_date(row.get("allocationEta")),
            to_sql_date(row.get("startingDate")),
            to_sql_date(row.get("completeDate")),
            _to_float(row.get("dailyWip")),
            _to_float(row.get("consumedMandays")),
            _to_float(row.get("savedMandays")),
            row.get("approvedVersion"),
            row.get("approvedBy"),
            row.get("comments"),
            row.get("complexity"),
            row.get("priority"),
            row.get("fromRoto"),
            row.get("fromPaint"),
            row.get("fromMm"),
            row.get("fromComp"),
        )
    return params
