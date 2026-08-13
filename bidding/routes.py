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
    """Check if user can access a department."""
    from common.constants import BROAD_ACCESS_ROLES

    return user and (user["role"] in BROAD_ACCESS_ROLES or user["department"] == department)


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
        query += " AND s.department = %s"
        params.append(department)
    else:
        # If no department specified, restrict to accessible departments
        from common.constants import BROAD_ACCESS_ROLES, DEPARTMENTS

        if user["role"] not in BROAD_ACCESS_ROLES:
            query += " AND s.department = %s"
            params.append(user["department"])

    # Status filter
    if status:
        query += " AND s.status = %s"
        params.append(status)

    query += " ORDER BY s.due_date ASC, s.department"

    rows = run_query(query, tuple(params), fetch_all=True) or []
    return success({"pendingBids": [shot_to_json(r) for r in rows]})


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
