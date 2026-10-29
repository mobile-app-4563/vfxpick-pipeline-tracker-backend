"""Production routes.

Production department concerns/issues tracker with editable cells.
Tracks concerns distinct from the actual shot data.
Includes role-based access control.
"""

from datetime import date, datetime, timedelta

from flask import Blueprint, request

from auth.middleware import token_required
from access.routes import (
    delete_enabled_for_user,
    import_enabled_for_user,
    menu_granted_for_user,
)
from common.constants import BROAD_ACCESS_ROLES
from common.audit import write_activity_log
from common.db_utils import (
    generate_prefixed_id,
    get_user,
    run_query,
    to_iso,
    to_sql_date,
)
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
               approved_client_md, fl_eta, fl_mandays, created_at
        FROM production_grid
        ORDER BY created_at ASC,
                 CAST(SUBSTRING(grid_id, 5) AS UNSIGNED) ASC
    """

    try:
        rows = run_query(query, fetch_all=True)
        grid = [_grid_to_json(row, idx + 1) for idx, row in enumerate(rows)]
        return success({"rows": grid, "total": len(grid)})
    except Exception as e:
        return failure(f"Failed to fetch production grid: {e}", 500)


@production_bp.route("/pickouts", methods=["GET"])
@token_required
def production_grid_pickouts(current_user_id):
    """Production-grid rows whose ETA falls today or tomorrow (Home pickouts).

    The Home page's "Production Pickouts" list is sourced from the imported
    Jan-Dec working file (production_grid), exactly like the Project Pickouts
    list is sourced from the shots table. Only rows whose ETA month/day
    equals today or tomorrow are returned (the stored year of imported Excel
    dates is meaningless and ignored), ordered by ETA then show/shot.
    """
    user = get_user(current_user_id)
    if not _accessible_roles(user):
        return failure("Access denied", 403)

    today_md = date.today().strftime("%m-%d")
    tomorrow_md = (date.today() + timedelta(days=1)).strftime("%m-%d")

    try:
        rows = run_query(
            """
            SELECT grid_id, coordinator, month, shots_received_date,
                   client_for_ref, client_name, show_name, wip_eta, eta,
                   shot_code, frames, tasks, review_notes, status,
                   delivered_on, work_station, shot_mandays,
                   approved_client_md, fl_eta, fl_mandays, created_at
            FROM production_grid
            WHERE DATE_FORMAT(eta, '%m-%d') IN (%s, %s)
            ORDER BY DATE_FORMAT(eta, '%m-%d') ASC, show_name ASC,
                     shot_code ASC
            """,
            (today_md, tomorrow_md),
            fetch_all=True,
        )
        pickouts = [_grid_to_json(row, idx + 1) for idx, row in enumerate(rows)]
        return success({"pickouts": pickouts, "total": len(pickouts)})
    except Exception as e:
        return failure(f"Failed to fetch production pickouts: {e}", 500)


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
                    # Normalise empty strings to NULL and validate dates so
                    # garbage text can never become MySQL 0000-00-00.
                    if db_column in _DATE_COLUMNS:
                        value = to_sql_date(value)
                    elif value in ("", None):
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
            write_activity_log(
                current_user_id,
                "Production Management",
                "UPDATE",
                "Production Grid",
                grid_id,
                {"fields": list(updates.keys()), "values": updates},
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

# Every column an import writes — the client/show/shot identity columns plus the
# 20 template columns. Used to refresh an existing row in place, so a
# re-imported file overwrites the stored Tasks/Status/ETA/… values.
_GRID_IMPORT_COLUMNS = [
    "client_name",
    "show_name",
    "shot_code",
    *GRID_INSERT_COLUMNS,
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
    """The Status cell exactly as the spreadsheet spells it.

    Import is a faithful copy of the file, so the value is never mapped onto a
    fixed list and never replaced with a default: ``HOLD``, ``Hold as of now``
    and ``Delivererd`` are all stored as written. A blank cell becomes NULL.
    """
    return _grid_null(value)


# ─── Import row identity: source_row_ref ─────────────────────────────────────
# An imported row is identified by
#   (client, show, tasks, shot_code, review_notes, source_row_ref)
# where source_row_ref is the line's row number inside the imported file.
# The old key stopped at review_notes, which is NOT unique in real
# spreadsheets: the same shot + department + notes legitimately appears on
# many lines (one per element/submission), each with its own Frames, ETA,
# Shot man-days and Status. Those lines used to be merged into one row.
# See database/migration_012_production_grid_source_row_ref.sql for the
# measured numbers. Hand-created rows keep source_row_ref NULL and behave
# exactly as before.
_GRID_SOURCE_REF_READY = False


def _ensure_grid_source_row_ref(cursor):
    """Add production_grid.source_row_ref on demand (idempotent).

    Mirrors the create-on-demand convention used elsewhere in the backend so
    a database that has not run migration_012 by hand still works. Runs at
    most once per process.
    """
    global _GRID_SOURCE_REF_READY
    if _GRID_SOURCE_REF_READY:
        return
    cursor.execute(
        "SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'production_grid' "
        "AND COLUMN_NAME = 'source_row_ref'"
    )
    row = cursor.fetchone()
    if not row or int(row.get("n") or 0) == 0:
        cursor.execute(
            "ALTER TABLE production_grid "
            "ADD COLUMN source_row_ref VARCHAR(80) DEFAULT NULL AFTER review_notes"
        )
        try:
            cursor.execute(
                "ALTER TABLE production_grid "
                "ADD INDEX idx_production_grid_source_ref (source_row_ref)"
            )
        except Exception:
            # The index is an optimisation only - never fail an import for it.
            pass
    _GRID_SOURCE_REF_READY = True


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
        to_sql_date(row.get("shotsReceivedDate")),
        row.get("clientForRef"),
        to_sql_date(row.get("wipEta")),
        to_sql_date(row.get("eta")),
        _grid_int(row.get("frames")),
        row.get("tasks"),
        row.get("reviewNotes"),
        status,
        to_sql_date(row.get("deliveredOn")),
        row.get("workStation"),
        _grid_float(row.get("shotMandays")),
        _grid_float(row.get("approvedClientMd")),
        to_sql_date(row.get("flEta")),
        _grid_float(row.get("flMandays")),
    )


@production_bp.route("/grid", methods=["POST"])
@token_required
def create_production_grid_row(current_user_id):
    """Manually create a new production-grid row (own table).

    Body: {"client": "...", "show": "...", "shotCode": "...",
           "tasks": "ROTO", ...grid fields...}
    Client/show are stored as plain names in production_grid (no dependency
    on the shared clients/shows tables). If a HAND-CREATED row with the same
    (client_name, show_name, tasks, shot_code, review_notes) already exists it
    is UPDATED with the incoming data instead of creating a duplicate.

    Imported rows are never matched here: they carry a ``source_row_ref``
    (the line number in the imported file) and each one is a distinct line of
    the spreadsheet. Without this restriction a manual add could silently
    overwrite an arbitrary imported line.
    """
    user = get_user(current_user_id)
    if not _can_edit_concern(user):
        return failure("Access denied", 403)
    if not import_enabled_for_user(user):
        return failure("Access denied: import is disabled for your department", 403)

    data = request.get_json(silent=True) or {}
    client_name = _grid_null(data.get("client")) or ""
    show_name = _grid_null(data.get("show")) or ""
    shot_code = _grid_null(data.get("shotCode"))
    tasks = _grid_null(data.get("tasks")) or _grid_null(data.get("department"))
    if not shot_code or not tasks:
        return failure("shotCode and tasks (department) are required", 400)

    status = _grid_status(data.get("status"))
    params = _grid_db_params(data, status)
    review_notes = _grid_null(data.get("reviewNotes")) or ""

    conn = get_db()
    try:
        cursor = conn.cursor(dictionary=True, buffered=True)

        # The lookup below filters on source_row_ref, so make sure the column
        # exists even on a database that predates migration_012.
        _ensure_grid_source_row_ref(cursor)

        # Idempotency is scoped to hand-created rows only (source_row_ref IS
        # NULL). Imported rows each own their own source_row_ref, so adding a
        # row by hand can never overwrite one of them.
        cursor.execute(
            "SELECT grid_id FROM production_grid "
            "WHERE client_name = %s AND show_name = %s AND tasks = %s "
            "AND shot_code = %s AND COALESCE(review_notes, '') = COALESCE(%s, '') "
            "AND source_row_ref IS NULL",
            (client_name, show_name, tasks, shot_code, review_notes),
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
            write_activity_log(
                current_user_id,
                "Production Management",
                "UPDATE",
                "Production Grid",
                existing["grid_id"],
                {"source": "manual create", "status": status},
            )
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
        write_activity_log(
            current_user_id,
            "Production Management",
            "CREATE",
            "Production Grid",
            grid_id,
            {"source": "manual create", "status": status},
        )
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
    """Import production-grid rows from an Excel/CSV file (true upsert).

    Body: {"rows": [ {grid fields + client/show names + sourceRowRef}, ... ]}
    Client/show are stored as plain names in production_grid — no dependency
    on the shared clients/shows tables.

    Row identity is ``source_row_ref`` — the physical line of the imported file
    (``Sheet1!12``, ``csv:12``, ``paste:12``). One file line is one grid row, so
    re-importing the same file **updates** those rows in place instead of
    appending duplicates. That is what keeps the grid in step with the
    spreadsheet: a corrected Tasks, Status, ETA or man-days cell in the file is
    written over the stored value on the next import.

    Nothing about the cell contents is validated, mapped or defaulted: the
    spreadsheet is the source of truth and every column — ``tasks`` included —
    is written exactly as the file spells it. Rows are never rejected for a
    missing shotCode, department, client or show.

    Rows without a ``source_row_ref`` (hand-created rows, and clients that do
    not send one) are inserted, since they have no file line to match.
    """
    user = get_user(current_user_id)
    if not _can_edit_concern(user):
        return failure("Access denied", 403)
    if not import_enabled_for_user(user):
        return failure("Access denied: import is disabled for your department", 403)

    data = request.get_json(silent=True) or {}
    rows = data.get("rows") or []
    if not isinstance(rows, list) or not rows:
        return failure("rows is required and must be a non-empty array.", 400)

    # ── Prepare every row without identity validation ──────────────────────
    # Do not use shotCode, client, show, or tasks as an import gate. Real
    # spreadsheets contain partial rows, and each physical non-empty row must
    # still be represented in the database.
    valid_rows = []
    errors = []
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            errors.append(f"Row {idx}: invalid row format.")
            continue
        shot_code = _grid_null(row.get("shotCode"))
        tasks = _grid_null(row.get("tasks")) or _grid_null(row.get("department"))
        source_ref = _grid_null(row.get("sourceRowRef")) or ""
        valid_rows.append((idx, row, shot_code, tasks, source_ref))

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

        # Add production_grid.source_row_ref if this database predates
        # migration_012. Runs before any write, so the implicit commit MySQL
        # performs for DDL cannot disturb the import transaction.
        _ensure_grid_source_row_ref(cursor)

        insert_sql = f"""
            INSERT INTO production_grid
                (grid_id, client_name, show_name, shot_code, source_row_ref,
                 {", ".join(GRID_INSERT_COLUMNS)})
            VALUES (%s, %s, %s, %s, %s, {", ".join(["%s"] * len(GRID_INSERT_COLUMNS))})
        """
        update_sql = f"""
            UPDATE production_grid
            SET {", ".join(f"{column} = %s" for column in _GRID_IMPORT_COLUMNS)},
                updated_at = CURRENT_TIMESTAMP
            WHERE grid_id = %s
        """

        # Existing rows that came from a spreadsheet line, grouped by that
        # line. A repeated import of the same file can leave several rows on
        # one line; every one of them is refreshed so the grid cannot keep
        # showing an older Tasks/Status value.
        existing = {}
        cursor.execute(
            "SELECT grid_id, source_row_ref FROM production_grid "
            "WHERE source_row_ref IS NOT NULL AND TRIM(source_row_ref) <> '' "
            "ORDER BY CAST(SUBSTRING(grid_id, 5) AS UNSIGNED) ASC"
        )
        for found in cursor.fetchall():
            key = (found.get("source_row_ref") or "").strip()
            existing.setdefault(key, []).append(found["grid_id"])
        duplicated = {ref: ids for ref, ids in existing.items() if len(ids) > 1}
        if duplicated:
            notes.append(
                f"{len(duplicated)} spreadsheet line(s) already had more than "
                "one grid row; all of them were refreshed from this file."
            )

        for idx, row, shot_code, tasks, source_ref in valid_rows:
            client_name = _grid_null(row.get("client")) or ""
            show_name = _grid_null(row.get("show")) or ""
            try:
                status = _grid_status(row.get("status"))
                row_for_insert = dict(row)
                row_for_insert["tasks"] = tasks
                params = _grid_db_params(row_for_insert, status)
                values = (client_name, show_name, shot_code, *params)

                matched = existing.get(source_ref) if source_ref else None
                if matched:
                    cursor.executemany(
                        update_sql, [(*values, grid_id) for grid_id in matched]
                    )
                    write_activity_log(
                        current_user_id,
                        "Production Management",
                        "UPDATE",
                        "Production Grid",
                        matched[0],
                        {"source": "bulk import", "status": status},
                    )
                    updated += len(matched)
                    continue

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
                    (
                        grid_id,
                        client_name,
                        show_name,
                        shot_code,
                        source_ref or None,
                        *params,
                    ),
                )
                if source_ref:
                    existing[source_ref] = [grid_id]
                write_activity_log(
                    current_user_id,
                    "Production Management",
                    "CREATE",
                    "Production Grid",
                    grid_id,
                    {"source": "bulk import", "status": status},
                )
                created += 1
            except Exception as e:
                errors.append(f"Row {idx}: {e}")

        conn.commit()
    except Exception as e:
        conn.rollback()
        return failure(f"Failed to import grid rows: {e}", 500)
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


@production_bp.route("/grid/<grid_id>", methods=["DELETE"])
@token_required
def delete_production_grid_row(current_user_id, grid_id):
    """Delete a single production-grid row by its grid_id. Allowed for
    users who can access the grid AND whose department has delete enabled
    (per-department switch from the Access Provider page)."""
    user = get_user(current_user_id)
    if not delete_enabled_for_user(user):
        return failure(
            "Access denied: delete is disabled for your department", 403
        )

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(buffered=True)
        cursor.execute("DELETE FROM production_grid WHERE grid_id = %s", [grid_id])
        if not cursor.rowcount:
            return failure("Production grid row not found", 404)
        write_activity_log(
            current_user_id,
            "Production Management",
            "DELETE",
            "Production Grid",
            grid_id,
        )
        return success(
            {"message": "Grid row deleted successfully", "gridId": grid_id}
        )
    except Exception as e:
        return failure(f"Failed to delete grid row: {e}", 500)
    finally:
        if conn is not None:
            conn.close()


@production_bp.route("/grid/bulk-delete", methods=["POST"])
@token_required
def bulk_delete_production_grid(current_user_id):
    """Delete multiple production-grid rows in a single request.

    Body: {"gridIds": ["GRID1", "GRID2", ...]}
    Runs in one transaction; returns deleted/skipped counts. Skipped
    rows are grid_ids that do not exist in the table.
    """
    user = get_user(current_user_id)
    if not delete_enabled_for_user(user):
        return failure(
            "Access denied: delete is disabled for your department", 403
        )

    data = request.get_json(silent=True) or {}
    grid_ids = data.get("gridIds") or []
    if not isinstance(grid_ids, list) or not grid_ids:
        return failure("gridIds (list) is required.", 400)

    conn = get_db()
    deleted = 0
    skipped = 0
    try:
        conn.autocommit = False
        cursor = conn.cursor(buffered=True)
        for grid_id in grid_ids:
            cursor.execute(
                "DELETE FROM production_grid WHERE grid_id = %s", (grid_id,)
            )
            if cursor.rowcount:
                deleted += 1
                write_activity_log(
                    current_user_id,
                    "Production Management",
                    "DELETE",
                    "Production Grid",
                    grid_id,
                    {"source": "bulk delete"},
                )
            else:
                skipped += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        return failure(f"Failed to bulk delete grid rows: {e}", 500)
    finally:
        conn.close()

    return success(
        {
            "message": f"Deleted {deleted} row(s), skipped {skipped}.",
            "deleted": deleted,
            "skipped": skipped,
        }
    )


def _accessible_roles(user):
    """Return True if user can access production module."""
    if not user:
        return False
    if user["role"] in BROAD_ACCESS_ROLES or user["department"] == "Production":
        return True
    # Access Provider matrix: a user whose role AND department have the
    # production-management menu enabled can view production data too, so
    # "giving all permissions" actually opens the screens it grants.
    return menu_granted_for_user(user, "/production-management")


def _can_edit_concern(user):
    """Return True if user can edit production concerns."""
    if not user:
        return False
    if user["role"] in BROAD_ACCESS_ROLES or user["department"] == "Production":
        return True
    return menu_granted_for_user(user, "/production-management")


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
        write_activity_log(
            current_user_id,
            "Production",
            "CREATE",
            "Production Concern",
            production_id,
            {"status": status, "priority": priority},
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
        write_activity_log(
            current_user_id,
            "Production",
            "UPDATE",
            "Production Concern",
            production_id,
            {"fields": list(data.keys()), "values": data},
        )
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
        write_activity_log(
            current_user_id,
            "Production",
            "DELETE",
            "Production Concern",
            production_id,
        )
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
