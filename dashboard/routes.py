"""Dashboard routes.

Department-wise shot tracking tables (ROTO, PAINT, MM, COMP) plus the
"Today's Target" summary panel, and an expandable shot list per show.
"""

from flask import Blueprint, request

from auth.middleware import token_required
from common.constants import DEPARTMENTS
from common.db_utils import run_query, to_iso
from common.http import success, failure
from common.serializers import SHOT_SELECT, shot_to_json

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/summary", methods=["GET"])
@token_required
def summary(_current_user_id):
    """Per-department breakdown grouped by client + show."""
    rows = run_query(
        """
        SELECT s.department,
               sh.client_id,
               c.client_name,
               sh.show_id,
               sh.show_name,
               COUNT(*)            AS shot_count,
               MIN(s.due_date)     AS due_date,
               SUM(s.client_bid)   AS mandays
        FROM shots s
        JOIN shows sh  ON s.show_id = sh.show_id
        JOIN clients c ON sh.client_id = c.client_id
        GROUP BY s.department, sh.client_id, c.client_name, sh.show_id, sh.show_name
        ORDER BY s.department, sh.client_id
        """,
        fetch_all=True,
    ) or []

    departments = []
    for dept in DEPARTMENTS:
        dept_rows = [r for r in rows if r["department"] == dept]
        table = [
            {
                "clientId": r["client_id"],
                "clientName": r["client_name"],
                "showId": r["show_id"],
                "showName": r["show_name"],
                "shotCount": int(r["shot_count"]),
                "dueDate": to_iso(r["due_date"]),
                "mandays": float(r["mandays"]) if r["mandays"] is not None else 0.0,
            }
            for r in dept_rows
        ]
        departments.append(
            {
                "department": dept,
                "rows": table,
                "target": {
                    "shows": len({r["show_id"] for r in dept_rows}),
                    "totalShotCount": sum(int(r["shot_count"]) for r in dept_rows),
                },
            }
        )

    return success({"departments": departments})


@dashboard_bp.route("/show/<show_id>/shots", methods=["GET"])
@token_required
def show_shots(_current_user_id, show_id):
    """Expandable list of shots for a show (optionally filtered by department)."""
    department = request.args.get("department")
    query = SHOT_SELECT + " WHERE s.show_id = %s"
    params = [show_id]
    if department:
        query += " AND s.department = %s"
        params.append(department)
    query += " ORDER BY s.shot_code"

    rows = run_query(query, tuple(params), fetch_all=True) or []
    if not rows:
        show = run_query("SELECT show_id FROM shows WHERE show_id = %s", (show_id,), fetch_one=True)
        if not show:
            return failure("Show not found", 404)
    return success({"shots": [shot_to_json(r) for r in rows]})


@dashboard_bp.route("/today-pickouts", methods=["GET"])
@token_required
def today_pickouts(_current_user_id):
    """Today's pickouts prioritized by urgency: due_date, department, pending bids."""
    from datetime import date

    today = str(date.today())
    query = (
        SHOT_SELECT
        + """
    WHERE DATE(s.allocated_date) = %s
    ORDER BY s.due_date ASC, s.department, 
             CASE WHEN s.supervisor_bid = 0 THEN 0 ELSE 1 END ASC
    """
    )

    rows = run_query(query, (today,), fetch_all=True) or []
    return success({"pickouts": [shot_to_json(r) for r in rows]})


@dashboard_bp.route("/artist-performance", methods=["GET"])
@token_required
def artist_performance(_current_user_id):
    """Performance chart data for all active artists."""
    rows = run_query(
        """
        SELECT u.user_id,
               u.name,
               u.department,
               COUNT(s.shot_id) AS total_shots,
               COALESCE(SUM(s.mandays), 0) AS total_mandays,
               SUM(CASE WHEN s.supervisor_status = 'Approved' THEN 1 ELSE 0 END) AS approved_shots
        FROM users u
        LEFT JOIN shots s ON s.artist_id = u.user_id
        WHERE u.role = 'Artist' AND u.status = 'Active'
        GROUP BY u.user_id, u.name, u.department
        ORDER BY total_mandays DESC, total_shots DESC, u.name ASC
        """,
        fetch_all=True,
    ) or []

    performers = [
        {
            "userId": r["user_id"],
            "name": r["name"],
            "department": r["department"],
            "totalShots": int(r["total_shots"] or 0),
            "totalMandays": float(r["total_mandays"] or 0.0),
            "approvedShots": int(r["approved_shots"] or 0),
        }
        for r in rows
    ]
    return success({"performers": performers})
