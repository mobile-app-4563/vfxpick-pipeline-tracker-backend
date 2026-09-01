"""Dashboard routes.

Department-wise shot tracking tables (ROTO, PAINT, MM, COMP) plus the
"Today's Target" summary panel, and an expandable shot list per show.
"""

from datetime import date, datetime, timedelta

from flask import Blueprint, request

from common.cache_instance import cache
from auth.middleware import token_required
from common.constants import DEPARTMENTS
from common.db_utils import run_query, to_iso
from common.http import success, failure
from common.serializers import SHOT_SELECT, shot_to_json

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/home-summary", methods=["GET"])
@token_required
def home_summary(_current_user_id):
    """Today/tomorrow pickout counts and active shows for the Home page.

    Computed from the production management grid (the Jan-Dec working file):
    a "pickout" is a grid row whose ETA falls today/tomorrow, and active shows
    are the distinct client/show groups with the earliest ETA. Cached for
    2 minutes. Pass ?refresh=1 to bypass.
    """
    if request.args.get("refresh") != "1":
        cached = cache.get("dash_home_summary")
        if cached is not None:
            return cached

    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    counts = run_query(
        """
        SELECT
            SUM(CASE WHEN eta = %s THEN 1 ELSE 0 END) AS today_pickouts,
            SUM(CASE WHEN eta = %s THEN 1 ELSE 0 END) AS tomorrow_pickouts
        FROM production_grid
        """,
        (today, tomorrow),
        fetch_one=True,
    ) or {}

    shows = run_query(
        """
        SELECT client_name, show_name, MIN(eta) AS eta
        FROM production_grid
        WHERE eta IS NOT NULL AND TRIM(eta) <> ''
        GROUP BY client_name, show_name
        ORDER BY eta ASC
        LIMIT 50
        """,
        fetch_all=True,
    ) or []

    result = {
        "todayPickouts": int(counts.get("today_pickouts") or 0),
        "tomorrowPickouts": int(counts.get("tomorrow_pickouts") or 0),
        "activeShows": [
            {
                "client": r["client_name"],
                "show": r["show_name"],
                "eta": to_iso(r["eta"]),
            }
            for r in shows
        ],
    }
    response = success(result)
    cache.set("dash_home_summary", response, timeout=120)
    return response


@dashboard_bp.route("/invent-active-shows", methods=["GET"])
@token_required
def invent_active_shows(_current_user_id):
    """Approved/Approved Internal shows for Home with expandable details.

    Cached for 2 minutes — this is the heaviest endpoint on the home page.
    Pass ?refresh=1 to bypass cache.
    """
    if request.args.get("refresh") != "1":
        cached = cache.get("dash_invent_active")
        if cached is not None:
            return cached

    statuses = ["Approved", "Approved Internal"]

    rows = run_query(
        """
        SELECT s.status,
               sh.show_id,
               sh.show_name,
               sh.client_id,
               c.client_name,
               COUNT(*) AS shot_count,
               COALESCE(SUM(s.mandays), 0) AS total_mandays,
               MIN(s.due_date) AS min_due_date,
               MAX(s.due_date) AS max_due_date,
               GROUP_CONCAT(DISTINCT s.department ORDER BY s.department SEPARATOR ',') AS departments,
               MAX(s.updated_at) AS last_updated_at
        FROM shots s
        JOIN shows sh ON s.show_id = sh.show_id
        JOIN clients c ON sh.client_id = c.client_id
        WHERE s.status IN (%s, %s)
        GROUP BY s.status, sh.show_id, sh.show_name, sh.client_id, c.client_name
        ORDER BY FIELD(s.status, 'Approved', 'Approved Internal'), sh.show_name
        """,
        tuple(statuses),
        fetch_all=True,
    ) or []

    shot_rows = run_query(
        SHOT_SELECT
        + """
        WHERE s.status IN (%s, %s)
        ORDER BY FIELD(s.status, 'Approved', 'Approved Internal'), sh.show_name, s.shot_code
        """,
        tuple(statuses),
        fetch_all=True,
    ) or []

    shots_by_group = {}
    for shot in shot_rows:
        key = (shot.get("status"), shot.get("show_id"))
        shots_by_group.setdefault(key, []).append(shot_to_json(shot))

    grouped = {status: [] for status in statuses}
    for row in rows:
        status = row.get("status")
        if status not in grouped:
            continue
        departments = (row.get("departments") or "")
        key = (status, row.get("show_id"))
        grouped[status].append(
            {
                "showId": row.get("show_id"),
                "showName": row.get("show_name"),
                "clientId": row.get("client_id"),
                "clientName": row.get("client_name"),
                "status": status,
                "shotCount": int(row.get("shot_count") or 0),
                "totalMandays": float(row.get("total_mandays") or 0),
                "minDueDate": to_iso(row.get("min_due_date")),
                "maxDueDate": to_iso(row.get("max_due_date")),
                "departments": [d for d in departments.split(",") if d],
                "lastUpdatedAt": to_iso(row.get("last_updated_at")),
                "shots": shots_by_group.get(key, []),
            }
        )

    result = {
        "statuses": [
            {"status": status, "shows": grouped.get(status, [])}
            for status in statuses
        ]
    }

    response = success(result)
    cache.set("dash_invent_active", response, timeout=120)
    return response


@dashboard_bp.route("/summary", methods=["GET"])
@token_required
def summary(_current_user_id):
    """Per-department breakdown grouped by client + show.

    Cached for 2 minutes. Pass ?refresh=1 to bypass.
    """
    if request.args.get("refresh") != "1":
        cached = cache.get("dash_summary")
        if cached is not None:
            return cached

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

    result = {"departments": departments}
    response = success(result)
    cache.set("dash_summary", response, timeout=120)
    return response


@dashboard_bp.route("/show/<show_id>/shots", methods=["GET"])
@token_required
def show_shots(_current_user_id, show_id):
    """Expandable list of shots for a show (optionally filtered by department)."""
    department = request.args.get("department")
    query = SHOT_SELECT + " WHERE s.show_id = %s"
    params = [show_id]
    if department:
        dept_parts = [d.strip() for d in department.split(",") if d.strip()]
        if dept_parts:
            dept_clause = " OR ".join(["FIND_IN_SET(%s, s.department)"] * len(dept_parts))
            query += f" AND ({dept_clause})"
            params.extend(dept_parts)
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
    """Today's pickouts prioritized by urgency: due_date, department, pending bids.

    Cached for 2 minutes per date. Pass ?refresh=1 to bypass.
    """
    date_param = (request.args.get("date") or "").strip()
    if date_param:
        try:
            today = datetime.strptime(date_param, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return failure("Invalid date format. Use YYYY-MM-DD.", 400)
    else:
        today = date.today().isoformat()

    cache_key = f"dash_pickouts_{today}"
    if request.args.get("refresh") != "1":
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    query = (
        SHOT_SELECT
        + """
    WHERE DATE(s.allocated_date) = %s
       OR DATE(s.due_date) = %s
       OR DATE(s.client_eta) = %s
    ORDER BY COALESCE(s.due_date, s.client_eta) ASC, s.department, 
             CASE WHEN s.supervisor_bid = 0 THEN 0 ELSE 1 END ASC
    """
    )

    rows = run_query(query, (today, today, today), fetch_all=True) or []
    result = {"pickouts": [shot_to_json(r) for r in rows]}
    response = success(result)
    cache.set(cache_key, response, timeout=120)
    return response


@dashboard_bp.route("/artist-performance", methods=["GET"])
@token_required
def artist_performance(_current_user_id):
    """Performance chart data for all active artists.

    Cached for 2 minutes. Pass ?refresh=1 to bypass.
    """
    if request.args.get("refresh") != "1":
        cached = cache.get("dash_artist_perf")
        if cached is not None:
            return cached

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
    result = {"performers": performers}
    response = success(result)
    cache.set("dash_artist_perf", response, timeout=120)
    return response
