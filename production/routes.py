"""Production routes.

Production department concerns/issues tracker with editable cells.
Tracks concerns distinct from the actual shot data.
Includes role-based access control.
"""

from datetime import datetime

from flask import Blueprint, request

from auth.middleware import token_required
from common.constants import BROAD_ACCESS_ROLES, DEPARTMENTS, SHOT_STATUSES
from common.db_utils import generate_prefixed_id, get_user, run_query, to_iso
from common.http import failure, success
from database.connection import get_db

production_bp = Blueprint("production", __name__)

# ─── Production Management Grid (Excel template columns) ───────────────────
# Maps the 20 Excel-template columns to production_grid table columns.
# The grid lives in its own dedicated table (production_grid) so it never
# mixes with the Projects module's `shots` data.
GRID_FIELDS = {
    "coordinator": "coordinator",
    "month": "month",
    "shotsReceivedDate": "shots_received_date",
    "clientForRef": "client_for_ref",
    "wipEta": "wip_eta",
    "eta": "eta",
    "frames": "frames",
    "tasks": "tasks",
    "reviewNotes": "review_notes",
    "status": "status",
    "deliveredOn": "delivered_on",
    "workStation": "work_station",
    "shotMandays": "shot_mandays",
    "approvedClientMd": "approved_client_md",
    "flEta": "fl_eta",
    "flMandays": "fl_mandays",
}


def _grid_to_json(row, sno):
    """Convert a production_grid row into the 20-column grid payload."""
    if not row:
        return None
    return {
        "sNo": sno,
        "shotId": row.get("grid_id"),
        "coordinator": row.get("coordinator"),
        "month": row.get("month"),
        "shotsReceivedDate": to_iso(row.get("shots_received_date")),
        "clientForRef": row.get("client_for_ref"),
        "client": row.get("client_name"),
        "show": row.get("show_name"),
        "wipEta": to_iso(row.get("wip_eta")),
        "eta": to_iso(row.get("eta")),
        "shotCode": row.get("shot_code"),
        "frames": row.get("frames"),
        "tasks": row.get("tasks"),
        "reviewNotes": row.get("review_notes"),
        "status": row.get("status"),
        "deliveredOn": to_iso(row.get("delivered_on")),
        "workStation": row.get("work_station"),
        "shotMandays": float(row["shot_mandays"]) if row.get("shot_mandays") is not None else 0.0,
        "approvedClientMd": float(row["approved_client_md"]) if row.get("approved_client_md") is not None else 0.0,
        "flEta": to_iso(row.get("fl_eta")),
        "flMandays": float(row["fl_mandays"]) if row.get("fl_mandays") is not None else 0.0,
    }


@production_bp.route("/grid", methods=["GET"])
@token_required
def get_production_grid(current_user_id):
    """Get the full production management grid (all rows, own table)."""
    user = get_user(current_user_id)
    if not _accessible_roles(user):
        return failure("Access denied", 403)

    query = """
        SELECT grid_id, coordinator, month, shots_received_date, client_for_ref,
               client_name, show_name, wip_eta, eta, shot_code, frames, tasks,
               review_notes, status, delivered_on, work_station, shot_mandays,
               approved_client_md, fl_eta, fl_mandays
        FROM production_grid
        ORDER BY created_at ASC
    """

    try:
        rows = run_query(query, fetch_all=True)
        grid = [_grid_to_json(row, idx + 1) for idx, row in enumerate(rows)]
        return success({"rows": grid, "total": len(grid)})
    except Exception as e:
        return failure(f"Failed to fetch production grid: {e}", 500)


@production_bp.route("/grid/sync", methods=["POST"])
@token_required
def sync_production_grid(current_user_id):
    """Bulk-update edited grid cells on the production_grid table.

    Body: {"rows": [{"shotId": "...", "updates": {"coordinator": "...", ...}}]}
    Only keys present in GRID_FIELDS are applied.
    """
    user = get_user(current_user_id)
    if not _can_edit_concern(user):
        return failure("Access denied", 403)

    data = request.get_json() or {}
    rows = data.get("rows", [])
    if not rows:
        return failure("rows are required", 400)

    updated_count = 0
    errors = []

    for idx, entry in enumerate(rows):
        try:
            grid_id = entry.get("shotId")
            updates = entry.get("updates", {})
            if not grid_id or not updates:
                errors.append({"row": idx, "error": "shotId and updates are required"})
                continue

            sets = []
            params = []
            for client_key, db_column in GRID_FIELDS.items():
                if client_key in updates:
                    value = updates[client_key]
                    # Normalise empty strings to NULL for date columns
                    if db_column in _DATE_COLUMNS and value in ("", None):
                        value = None
                    sets.append(f"{db_column} = %s")
                    params.append(value)

            if not sets:
                errors.append({"row": idx, "error": "no editable fields supplied"})
                continue

            sets.append("updated_at = CURRENT_TIMESTAMP")
            params.append(grid_id)

            run_query(
                f"UPDATE production_grid SET {', '.join(sets)} WHERE grid_id = %s",
                params,
            )
            updated_count += 1
        except Exception as e:
            errors.append({"row": idx, "error": str(e)})

    return success(
        {
            "updated": updated_count,
            "errors": errors,
            "total": len(rows),
        }
    )


# ─── Grid import / manual creation helpers ────────────────────────────────────

# Columns persisted for the production management grid (in INSERT/UPDATE order).
GRID_INSERT_COLUMNS = [
    "coordinator",
    "month",
    "shots_received_date",
    "client_for_ref",
    "wip_eta",
    "eta",
    "frames",
    "tasks",
    "review_notes",
    "status",
    "delivered_on",
    "work_station",
    "shot_mandays",
    "approved_client_md",
    "fl_eta",
    "fl_mandays",
]

# Grid JSON key -> DB column (a superset of GRID_FIELDS including shotCode).
# Note: GRID_FIELDS (above) is the live mapping used by /grid/sync; this map
# is retained for reference/import tooling.
_DATE_COLUMNS = {
    "shots_received_date",
    "wip_eta",
    "eta",
    "delivered_on",
    "fl_eta",
}


def _grid_null(value):
    """Normalise empty/placeholder strings to None (mirrors the sync flow)."""
    if value is None:
        return None
    text = str(value).strip()
    if text in ("", "-", "--", "n/a", "na", "null", "none", "None"):
        return None
    return text


def _grid_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _grid_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _grid_status(value):
    status = _grid_null(value) or "Awaiting Approval"
    return status if status in SHOT_STATUSES else "Awaiting Approval"


def _next_prefixed_id(cursor, table, id_column, prefix, id_state, key):
    """Generate sequential prefixed IDs within one request/transaction.

    The next number is MAX(numeric suffix)+1 (not COUNT), so gaps or deletions
    never cause collisions, and it runs on the SAME transaction cursor so
    auto-created rows in flight are visible and IDs never collide within a
    batch.
    """
    if id_state.get(key) is None:
        cursor.execute(
            f"SELECT COALESCE(MAX(CAST(SUBSTRING({id_column}, %s) AS UNSIGNED)), 0) "
            f"AS max_id FROM {table}",
            (len(prefix) + 1,),
        )
        row = cursor.fetchone()
        id_state[key] = int(row["max_id"] or 0) + 1
    seq = id_state[key]
    id_state[key] = seq + 1
    return f"{prefix}{seq}"


def _grid_db_params(row, status):
    """Convert a grid JSON row into the flat param tuple for INSERT/UPDATE."""
    return (
        row.get("coordinator"),
        row.get("month"),
        row.get("shotsReceivedDate"),
        row.get("clientForRef"),
        row.get("wipEta"),
        row.get("eta"),
        _grid_int(row.get("frames")),
        row.get("tasks"),
        row.get("reviewNotes"),
        status,
        row.get("deliveredOn"),
        row.get("workStation"),
        _grid_float(row.get("shotMandays")),
        _grid_float(row.get("approvedClientMd")),
        row.get("flEta"),
        _grid_float(row.get("flMandays")),
    )


@production_bp.route("/grid", methods=["POST"])
@token_required
def create_production_grid_row(current_user_id):
    """Manually create a new production-grid row (own table).

    Body: {"client": "...", "show": "...", "shotCode": "...",
           "tasks": "ROTO", ...grid fields...}
    Client/show are stored as plain names in production_grid (no dependency
    on the shared clients/shows tables). If a row with the same
    (client_name, show_name, tasks, shot_code) already exists it is UPDATED
    with the incoming data instead of creating a duplicate.
    """
    user = get_user(current_user_id)
    if not _can_edit_concern(user):
        return failure("Access denied", 403)

    data = request.get_json(silent=True) or {}
    client_name = _grid_null(data.get("client")) or ""
    show_name = _grid_null(data.get("show")) or ""
    shot_code = _grid_null(data.get("shotCode"))
    tasks = _grid_null(data.get("tasks")) or _grid_null(data.get("department"))
    if tasks:
        tasks = tasks.upper()

    if not shot_code or not tasks:
        return failure("shotCode and tasks (department) are required", 400)
    if tasks not in DEPARTMENTS:
        return failure(f"Invalid department '{tasks}'.", 400)

    conn = get_db()
    try:
        cursor = conn.cursor(dictionary=True, buffered=True)

        # Upsert: a row with the same (client, show, tasks, shot_code) already
        # exists → update it with the incoming data instead of duplicating it.
        cursor.execute(
            "SELECT grid_id FROM production_grid "
            "WHERE client_name = %s AND show_name = %s AND tasks = %s AND shot_code = %s",
            (client_name, show_name, tasks, shot_code),
        )
        existing = cursor.fetchone()
        if existing:
            update_sql = f"""
                UPDATE production_grid
                SET {', '.join([f'{col} = %s' for col in GRID_INSERT_COLUMNS])},
                    updated_at = CURRENT_TIMESTAMP
                WHERE grid_id = %s
            """
            cursor.execute(update_sql, (*params, existing["grid_id"]))
            conn.commit()
            return success(
                {
                    "shotId": existing["grid_id"],
                    "message": "Row already exists — updated with incoming data.",
                },
                200,
            )

        grid_id = _next_prefixed_id(
            cursor, "production_grid", "grid_id", "GRID", {}, "grid"
        )
        insert_sql = f"""
            INSERT INTO production_grid
                (grid_id, client_name, show_name, shot_code,
                 {", ".join(GRID_INSERT_COLUMNS)})
            VALUES (%s, %s, %s, %s, {", ".join(["%s"] * len(GRID_INSERT_COLUMNS))})
        """
        cursor.execute(
            insert_sql, (grid_id, client_name, show_name, shot_code, *params)
        )
        conn.commit()
        return success(
            {"shotId": grid_id, "message": "Row created successfully"},
            201,
        )
    except Exception as e:
        conn.rollback()
        return failure(f"Failed to create grid row: {e}", 500)
    finally:
        conn.close()


@production_bp.route("/grid/bulk-upsert", methods=["POST"])
@token_required
def bulk_upsert_production_grid(current_user_id):
    """Bulk create/update production-grid rows (from Excel/CSV import).

    Body: {"rows": [ {grid fields + client/show names}, ... ]}
    Client/show are stored as plain names in production_grid — no dependency
    on the shared clients/shows tables. Upserts on
    (client_name, show_name, tasks, shot_code). Duplicate rows WITHIN the same
    batch also update the first occurrence (never duplicated). Rows missing
    shotCode or an invalid department are reported in ``errors``.
    """
    user = get_user(current_user_id)
    if not _can_edit_concern(user):
        return failure("Access denied", 403)

    data = request.get_json(silent=True) or {}
    rows = data.get("rows") or []
    if not isinstance(rows, list) or not rows:
        return failure("rows is required and must be a non-empty array.", 400)

    # ── Pre-validate every row (no DB writes yet) ──────────────────────────
    valid_rows = []
    errors = []
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            errors.append(f"Row {idx}: invalid row format.")
            continue
        shot_code = _grid_null(row.get("shotCode"))
        tasks = _grid_null(row.get("tasks")) or _grid_null(row.get("department"))
        if tasks:
            tasks = tasks.upper()
        if not shot_code or not tasks:
            errors.append(f"Row {idx}: shotCode and tasks (department) are required.")
            continue
        if tasks not in DEPARTMENTS:
            errors.append(f"Row {idx}: invalid department '{tasks}'.")
            continue
        valid_rows.append((idx, row, shot_code, tasks))

    if not valid_rows:
        return success({"created": 0, "updated": 0, "errors": errors, "notes": []})

    conn = get_db()
    conn.autocommit = False
    created = 0
    updated = 0
    notes = []
    try:
        cursor = conn.cursor(dictionary=True, buffered=True)
        id_state = {}

        # ── Batch-fetch existing rows per (client, show, tasks) ────────────
        existing_map = {}
        unique_groups = {
            (
                _grid_null(row.get("client")) or "",
                _grid_null(row.get("show")) or "",
                tasks,
            )
            for idx, row, _, tasks in valid_rows
        }
        for client_name, show_name, tasks in unique_groups:
            cursor.execute(
                "SELECT grid_id, client_name, show_name, tasks, shot_code "
                "FROM production_grid WHERE client_name = %s AND show_name = %s AND tasks = %s",
                (client_name, show_name, tasks),
            )
            for r in cursor.fetchall():
                existing_map[
                    (r["client_name"], r["show_name"], r["tasks"], r["shot_code"])
                ] = r["grid_id"]

        update_sql = f"""
            UPDATE production_grid
            SET {", ".join([f"{col} = %s" for col in GRID_INSERT_COLUMNS])},
                updated_at = CURRENT_TIMESTAMP
            WHERE grid_id = %s
        """
        insert_sql = f"""
            INSERT INTO production_grid
                (grid_id, client_name, show_name, shot_code,
                 {", ".join(GRID_INSERT_COLUMNS)})
            VALUES (%s, %s, %s, %s, {", ".join(["%s"] * len(GRID_INSERT_COLUMNS))})
        """

        for idx, row, shot_code, tasks in valid_rows:
            client_name = _grid_null(row.get("client")) or ""
            show_name = _grid_null(row.get("show")) or ""
            try:
                status = _grid_status(row.get("status"))
                existing_grid_id = existing_map.get(
                    (client_name, show_name, tasks, shot_code)
                )
                params = _grid_db_params(row, status)
                if existing_grid_id:
                    cursor.execute(update_sql, (*params, existing_grid_id))
                    updated += 1
                else:
                    grid_id = _next_prefixed_id(
                        cursor,
                        "production_grid",
                        "grid_id",
                        "GRID",
                        id_state,
                        "grid",
                    )
                    cursor.execute(
                        insert_sql,
                        (grid_id, client_name, show_name, shot_code, *params),
                    )
                    # Remember in-batch inserts so a duplicate row later in the
                    # same file updates this row instead of duplicating it.
                    existing_map[(client_name, show_name, tasks, shot_code)] = grid_id
                    created += 1
            except Exception as e:
                errors.append(f"Row {idx}: {e}")

        conn.commit()
    except Exception as e:
        conn.rollback()
        return failure(f"Failed to bulk upsert grid: {e}", 500)
    finally:
        conn.close()

    return success(
        {
            "created": created,
            "updated": updated,
            "errors": errors,
            "notes": notes,
            "total": len(rows),
        }
    )


def _accessible_roles(user):
    """Return True if user can access production module."""
    if not user:
        return False
    if user["role"] in BROAD_ACCESS_ROLES or user["department"] == "Production":
        return True
    return False


def _can_edit_concern(user):
    """Return True if user can edit production concerns."""
    if not user:
        return False
    return user["role"] in BROAD_ACCESS_ROLES or user["department"] == "Production"


def _production_to_json(row):
    """Convert a production_data row to JSON."""
    if not row:
        return None
    return {
        "productionId": row["production_id"],
        "showId": row["show_id"],
        "shotId": row["shot_id"],
        "concernType": row["concern_type"],
        "concernDescription": row["concern_description"],
        "status": row["status"],
        "priority": row["priority"],
        "assignedTo": row["assigned_to"],
        "reportedBy": row["reported_by"],
        "reportedDate": row["reported_date"].isoformat() if row["reported_date"] else None,
        "dueDate": row["due_date"].isoformat() if row["due_date"] else None,
        "resolvedDate": row["resolved_date"].isoformat() if row["resolved_date"] else None,
        "plannedResolution": row["planned_resolution"],
        "actualResolution": row["actual_resolution"],
        "impactArea": row["impact_area"],
        "estimatedEffort": float(row["estimated_effort"]) if row["estimated_effort"] else 0,
        "actualEffort": float(row["actual_effort"]) if row["actual_effort"] else 0,
        "comments": row["comments"],
        "attachmentsUrl": row["attachments_url"],
        "department": row["department"],
        "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
        "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else None,
        "updatedBy": row["updated_by"],
    }


@production_bp.route("/concerns", methods=["GET"])
@token_required
def get_production_concerns(current_user_id):
    """Get production concerns (filtered by access level)."""
    user = get_user(current_user_id)
    if not _accessible_roles(user):
        return failure("Access denied", 403)

    show_id = request.args.get("showId", "")
    status_filter = request.args.get("status", "")
    priority_filter = request.args.get("priority", "")

    query = """
        SELECT production_id, show_id, shot_id, concern_type, concern_description, 
               status, priority, assigned_to, reported_by, reported_date, due_date, 
               resolved_date, planned_resolution, actual_resolution, impact_area, 
               estimated_effort, actual_effort, comments, attachments_url, department, 
               created_at, updated_at, updated_by
        FROM production_data
        WHERE 1 = 1
    """
    params = []

    if show_id:
        query += " AND show_id = %s"
        params.append(show_id)

    if status_filter:
        query += " AND status = %s"
        params.append(status_filter)

    if priority_filter:
        query += " AND priority = %s"
        params.append(priority_filter)

    query += " ORDER BY priority DESC, reported_date DESC"

    try:
        rows = run_query(query, params, fetch_all=True)
        concerns = [_production_to_json(row) for row in rows]
        return success({"concerns": concerns, "total": len(concerns)})
    except Exception as e:
        return failure(f"Failed to fetch production concerns: {e}", 500)


@production_bp.route("/concerns/<production_id>", methods=["GET"])
@token_required
def get_production_concern(current_user_id, production_id):
    """Get a single production concern by ID."""
    user = get_user(current_user_id)
    if not _accessible_roles(user):
        return failure("Access denied", 403)

    query = """
        SELECT production_id, show_id, shot_id, concern_type, concern_description, 
               status, priority, assigned_to, reported_by, reported_date, due_date, 
               resolved_date, planned_resolution, actual_resolution, impact_area, 
               estimated_effort, actual_effort, comments, attachments_url, department, 
               created_at, updated_at, updated_by
        FROM production_data
        WHERE production_id = %s
    """

    try:
        rows = run_query(query, [production_id], fetch_all=True)
        if not rows:
            return failure("Concern not found", 404)
        concern = _production_to_json(rows[0])
        return success({"concern": concern})
    except Exception as e:
        return failure(f"Failed to fetch concern: {e}", 500)


@production_bp.route("/concerns", methods=["POST"])
@token_required
def create_production_concern(current_user_id):
    """Create a new production concern."""
    user = get_user(current_user_id)
    if not _can_edit_concern(user):
        return failure("Access denied", 403)

    data = request.get_json() or {}
    show_id = data.get("showId", "")
    shot_id = data.get("shotId")
    concern_type = data.get("concernType", "")
    concern_description = data.get("concernDescription", "")
    status = data.get("status", "Open")
    priority = data.get("priority", "Medium")
    assigned_to = data.get("assignedTo")
    due_date = data.get("dueDate")
    planned_resolution = data.get("plannedResolution", "")
    impact_area = data.get("impactArea", "")

    if not show_id or not concern_type:
        return failure("showId and concernType are required", 400)

    production_id = generate_prefixed_id("production_data", "production_id", "PROD", 0)

    query = """
        INSERT INTO production_data 
        (production_id, show_id, shot_id, concern_type, concern_description, status, 
         priority, assigned_to, reported_by, due_date, planned_resolution, impact_area, department)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    try:
        run_query(
            query,
            [
                production_id,
                show_id,
                shot_id,
                concern_type,
                concern_description,
                status,
                priority,
                assigned_to,
                current_user_id,
                due_date,
                planned_resolution,
                impact_area,
                "Production",
            ],
        )
        return success(
            {"productionId": production_id, "message": "Concern created successfully"},
            201,
        )
    except Exception as e:
        return failure(f"Failed to create concern: {e}", 500)


@production_bp.route("/concerns/<production_id>", methods=["PUT"])
@token_required
def update_production_concern(current_user_id, production_id):
    """Update an existing production concern (editable cells)."""
    user = get_user(current_user_id)
    if not _can_edit_concern(user):
        return failure("Access denied", 403)

    data = request.get_json() or {}

    # Allowed editable fields
    updates = []
    params = []

    editable_fields = {
        "concernType": "concern_type",
        "concernDescription": "concern_description",
        "status": "status",
        "priority": "priority",
        "assignedTo": "assigned_to",
        "dueDate": "due_date",
        "resolvedDate": "resolved_date",
        "plannedResolution": "planned_resolution",
        "actualResolution": "actual_resolution",
        "impactArea": "impact_area",
        "estimatedEffort": "estimated_effort",
        "actualEffort": "actual_effort",
        "comments": "comments",
        "attachmentsUrl": "attachments_url",
    }

    for client_key, db_column in editable_fields.items():
        if client_key in data:
            updates.append(f"{db_column} = %s")
            params.append(data[client_key])

    if not updates:
        return failure("No valid fields to update", 400)

    updates.append("updated_at = CURRENT_TIMESTAMP")
    updates.append("updated_by = %s")
    params.append(current_user_id)

    params.append(production_id)

    query = f"""
        UPDATE production_data
        SET {", ".join(updates)}
        WHERE production_id = %s
    """

    try:
        run_query(query, params)
        return success({"message": "Concern updated successfully"})
    except Exception as e:
        return failure(f"Failed to update concern: {e}", 500)


@production_bp.route("/concerns/<production_id>", methods=["DELETE"])
@token_required
def delete_production_concern(current_user_id, production_id):
    """Delete a production concern."""
    user = get_user(current_user_id)
    if not _can_edit_concern(user):
        return failure("Access denied", 403)

    query = "DELETE FROM production_data WHERE production_id = %s"

    try:
        run_query(query, [production_id])
        return success({"message": "Concern deleted successfully"})
    except Exception as e:
        return failure(f"Failed to delete concern: {e}", 500)


@production_bp.route("/concerns/bulk-upsert", methods=["POST"])
@token_required
def bulk_upsert_concerns(current_user_id):
    """Bulk create/update production concerns (from Excel import)."""
    user = get_user(current_user_id)
    if not _can_edit_concern(user):
        return failure("Access denied", 403)

    data = request.get_json() or {}
    rows = data.get("rows", [])
    show_id = data.get("showId", "")

    if not rows or not show_id:
        return failure("rows and showId are required", 400)

    created_count = 0
    updated_count = 0
    errors = []

    for idx, row in enumerate(rows):
        try:
            production_id = row.get("productionId")
            concern_type = row.get("concernType", "")
            concern_description = row.get("concernDescription", "")
            status = row.get("status", "Open")
            priority = row.get("priority", "Medium")
            assigned_to = row.get("assignedTo")
            shot_id = row.get("shotId")

            if not concern_type:
                errors.append(
                    {
                        "row": idx,
                        "error": "concernType is required",
                    }
                )
                continue

            if production_id:
                # Update existing
                query = """
                    UPDATE production_data
                    SET concern_type = %s, concern_description = %s, status = %s,
                        priority = %s, assigned_to = %s, updated_at = CURRENT_TIMESTAMP,
                        updated_by = %s
                    WHERE production_id = %s
                """
                run_query(
                    query,
                    [
                        concern_type,
                        concern_description,
                        status,
                        priority,
                        assigned_to,
                        current_user_id,
                        production_id,
                    ],
                )
                updated_count += 1
            else:
                # Create new
                production_id = generate_prefixed_id(
                    "production_data", "production_id", "PROD", 0
                )
                query = """
                    INSERT INTO production_data 
                    (production_id, show_id, shot_id, concern_type, concern_description, 
                     status, priority, assigned_to, reported_by, department)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                run_query(
                    query,
                    [
                        production_id,
                        show_id,
                        shot_id,
                        concern_type,
                        concern_description,
                        status,
                        priority,
                        assigned_to,
                        current_user_id,
                        "Production",
                    ],
                )
                created_count += 1

        except Exception as e:
            errors.append(
                {
                    "row": idx,
                    "error": str(e),
                }
            )

    return success(
        {
            "created": created_count,
            "updated": updated_count,
            "errors": errors,
            "total": created_count + updated_count,
        }
    )
