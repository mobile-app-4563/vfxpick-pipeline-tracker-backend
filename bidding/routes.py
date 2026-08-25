"""Bidding routes.

Manages supervisor bids, client bids, and artist bids on shots.
Tracks bidding workflow: pending → approved → rejected.
"""

from flask import Blueprint, request

from auth.middleware import token_required
from common.db_utils import get_user, run_query
from common.http import failure, success
from common.serializers import SHOT_SELECT, shot_to_json

bidding_bp = Blueprint("bidding", __name__)


def _can_access(user, department):
    """Check if user can access a department (may be a comma-separated list)."""
    from common.constants import BROAD_ACCESS_ROLES

    if not user:
        return False
    if user["role"] in BROAD_ACCESS_ROLES:
        return True
    depts = [d.strip() for d in (department or "").split(",") if d.strip()]
    return user["department"] in depts


def _to_float(value, default=0.0):
    """Convert value to float, return default if invalid."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@bidding_bp.route("/pending", methods=["GET"])
@token_required
def list_pending_bids(current_user_id):
    """List all shots with incomplete bids (supervisor_bid=0 or client_bid=0).
    
    Optional query params:
    - department: Filter by department
    - status: Filter by shot status
    """
    user = get_user(current_user_id)
    department = request.args.get("department")
    status = request.args.get("status")

    query = SHOT_SELECT + " WHERE (s.supervisor_bid = 0 OR s.client_bid = 0)"
    params = []

    # Department filter with access control
    if department:
        if not _can_access(user, department):
            return failure("You are not allowed to access this department.", 403)
        dept_parts = [d.strip() for d in department.split(",") if d.strip()]
        dept_clause = " OR ".join(["FIND_IN_SET(%s, s.department)"] * len(dept_parts))
        query += f" AND ({dept_clause})"
        params.extend(dept_parts)
    else:
        # If no department specified, restrict to accessible departments
        from common.constants import BROAD_ACCESS_ROLES, DEPARTMENTS

        if user["role"] not in BROAD_ACCESS_ROLES:
            query += " AND FIND_IN_SET(%s, s.department)"
            params.append(user["department"])

    # Status filter
    if status:
        query += " AND s.status = %s"
        params.append(status)

    query += " ORDER BY s.due_date ASC, s.department"

    rows = run_query(query, tuple(params), fetch_all=True) or []
    return success({"pendingBids": [shot_to_json(r) for r in rows]})


@bidding_bp.route("/grid-pending", methods=["GET"])
@token_required
def list_grid_pending_bids(current_user_id):
    """List production-grid shots whose status is 'Bidding'.

    These come from the Production Management grid (JAN-DEC status column)
    so they live in the production_grid table, not the `shots` table.
    """
    from common.db_utils import to_iso

    rows = run_query(
        """
        SELECT grid_id, coordinator, month, shots_received_date, client_for_ref,
               client_name, show_name, wip_eta, eta, shot_code, frames, tasks,
               review_notes, status, delivered_on, work_station, shot_mandays,
               approved_client_md, fl_eta, fl_mandays
        FROM production_grid
        WHERE LOWER(TRIM(status)) = 'bidding'
        ORDER BY created_at ASC
        """,
        fetch_all=True,
    ) or []

    grid = []
    for idx, row in enumerate(rows):
        grid.append(
            {
                "sNo": idx + 1,
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
                "shotMandays": (
                    float(row["shot_mandays"])
                    if row.get("shot_mandays") is not None
                    else 0.0
                ),
                "approvedClientMd": (
                    float(row["approved_client_md"])
                    if row.get("approved_client_md") is not None
                    else 0.0
                ),
                "flEta": to_iso(row.get("fl_eta")),
                "flMandays": (
                    float(row["fl_mandays"])
                    if row.get("fl_mandays") is not None
                    else 0.0
                ),
            }
        )

    return success({"gridBids": grid, "total": len(grid)})


@bidding_bp.route("/shot/<shot_id>", methods=["GET"])
@token_required
def get_shot_bids(current_user_id, shot_id):
    """Get bidding details for a specific shot."""
    existing = run_query(
        "SELECT department FROM shots WHERE shot_id = %s", (shot_id,), fetch_one=True
    )
    if not existing:
        return failure("Shot not found", 404)

    user = get_user(current_user_id)
    if not _can_access(user, existing["department"]):
        return failure("You are not allowed to access this shot.", 403)

    row = run_query(SHOT_SELECT + " WHERE s.shot_id = %s", (shot_id,), fetch_one=True)
    if not row:
        return failure("Shot not found", 404)

    return success({"shot": shot_to_json(row)})


@bidding_bp.route("/shot/<shot_id>", methods=["PUT"])
@token_required
def update_shot_bids(current_user_id, shot_id):
    """Update supervisor_bid, client_bid, or artist_bid for a shot.
    
    Request JSON body (all fields optional):
    {
        "supervisorBid": 5.5,
        "clientBid": 6.0,
        "artistBid": 4.5
    }
    """
    existing = run_query(
        "SELECT department FROM shots WHERE shot_id = %s", (shot_id,), fetch_one=True
    )
    if not existing:
        return failure("Shot not found", 404)

    user = get_user(current_user_id)
    if not _can_access(user, existing["department"]):
        return failure("You are not allowed to modify this shot.", 403)

    data = request.get_json(silent=True) or {}
    field_map = {
        "supervisorBid": "supervisor_bid",
        "clientBid": "client_bid",
        "artistBid": "artist_bid",
    }

    sets = []
    params = []
    for json_key, column in field_map.items():
        if json_key in data:
            value = _to_float(data[json_key], default=0.0)
            sets.append(f"{column} = %s")
            params.append(value)

    if not sets:
        return failure("No bid fields to update.", 400)

    params.append(shot_id)
    run_query(f"UPDATE shots SET {', '.join(sets)} WHERE shot_id = %s", tuple(params))

    row = run_query(SHOT_SELECT + " WHERE s.shot_id = %s", (shot_id,), fetch_one=True)
    return success({"shot": shot_to_json(row)})


@bidding_bp.route("/shot/<shot_id>/approve", methods=["PATCH"])
@token_required
def approve_bid(current_user_id, shot_id):
    """Approve a bid (mark as completed/approved).
    
    Sets shot status to 'Approved' after all bids are finalized.
    """
    existing = run_query(
        "SELECT department, status FROM shots WHERE shot_id = %s", (shot_id,), fetch_one=True
    )
    if not existing:
        return failure("Shot not found", 404)

    user = get_user(current_user_id)
    if not _can_access(user, existing["department"]):
        return failure("You are not allowed to approve this bid.", 403)

    run_query(
        "UPDATE shots SET status = %s WHERE shot_id = %s",
        ("Approved", shot_id),
    )

    row = run_query(SHOT_SELECT + " WHERE s.shot_id = %s", (shot_id,), fetch_one=True)
    return success({"shot": shot_to_json(row)})


@bidding_bp.route("/shot/<shot_id>/reject", methods=["PATCH"])
@token_required
def reject_bid(current_user_id, shot_id):
    """Reject a bid and revert to 'Hold' status for re-bidding."""
    existing = run_query(
        "SELECT department FROM shots WHERE shot_id = %s", (shot_id,), fetch_one=True
    )
    if not existing:
        return failure("Shot not found", 404)

    user = get_user(current_user_id)
    if not _can_access(user, existing["department"]):
        return failure("You are not allowed to reject this bid.", 403)

    run_query(
        "UPDATE shots SET status = %s WHERE shot_id = %s",
        ("Hold", shot_id),
    )

    row = run_query(SHOT_SELECT + " WHERE s.shot_id = %s", (shot_id,), fetch_one=True)
    return success({"shot": shot_to_json(row)})
