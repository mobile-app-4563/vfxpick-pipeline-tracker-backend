from flask import Blueprint, request

from auth.middleware import token_required
from common.constants import USER_ROLES
from common.db_utils import get_user, run_query
from common.http import failure, success
from common.options_store import ROLE_CATEGORY, list_options, seed_options

access_bp = Blueprint("access", __name__)

ORDERED_MENU_ROUTES = [
    "/home",
    "/dashboard",
    "/bidding",
    "/projects",
    "/assets",
    "/tasks",
    "/review",
    "/feedback",
    "/reports",
    "/teams",
    "/notifications",
    "/user-register",
    "/hrms",
    "/access-provider",
    "/inventory",
]

_ARTIST_DEFAULTS = {
    "/home",
    "/dashboard",
    "/tasks",
    "/notifications",
}

_FULL_ACCESS_DEFAULTS = {
    "/home",
    "/dashboard",
    "/bidding",
    "/projects",
    "/assets",
    "/tasks",
    "/review",
    "/feedback",
    "/reports",
    "/teams",
    "/notifications",
    "/user-register",
    "/hrms",
    "/inventory",
}

_FULL_ACCESS_ROLES = {"Supervisor", "Team Lead", "Admin", "Production", "Management"}

_ROUTE_ALIASES = {
    "/register": "/user-register",
}


def _canonical_route(route):
    return _ROUTE_ALIASES.get(route, route)


def _apply_route_invariants(mapping):
    for role, routes in list(mapping.items()):
        route_set = set(routes or [])
        if role in _FULL_ACCESS_ROLES:
            route_set.add("/hrms")
        if role == "Admin":
            route_set.add("/access-provider")
        mapping[role] = sorted(route_set)
    return mapping


def _ensure_table():
    run_query(
        """
        CREATE TABLE IF NOT EXISTS role_menu_permissions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            role VARCHAR(50) NOT NULL,
            route VARCHAR(100) NOT NULL,
            is_allowed TINYINT(1) NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_role_route (role, route),
            INDEX idx_role_allowed (role, is_allowed)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    try:
        run_query(
            """
            DELETE legacy
            FROM role_menu_permissions AS legacy
            INNER JOIN role_menu_permissions AS canonical
                ON canonical.role = legacy.role
               AND canonical.route = %s
            WHERE legacy.route = %s
            """,
            ("/user-register", "/register"),
        )
        run_query(
            "UPDATE role_menu_permissions SET route = %s WHERE route = %s",
            ("/user-register", "/register"),
        )
    except Exception:
        pass
    run_query(
        """
        CREATE TABLE IF NOT EXISTS role_menu_permission_audit (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            changed_by_user_id VARCHAR(20) NOT NULL,
            action VARCHAR(30) NOT NULL,
            role VARCHAR(50) NOT NULL,
            route VARCHAR(100) NOT NULL,
            old_allowed TINYINT(1) NOT NULL,
            new_allowed TINYINT(1) NOT NULL,
            changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_audit_changed_at (changed_at DESC),
            INDEX idx_audit_actor (changed_by_user_id),
            INDEX idx_audit_role_route (role, route)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    try:
        has_other_routes = run_query(
            "SELECT 1 FROM role_menu_permissions WHERE route != %s LIMIT 1",
            ("/inventory",),
            fetch_all=True
        )
        if has_other_routes:
            existing = run_query(
                "SELECT 1 FROM role_menu_permissions WHERE route = %s LIMIT 1",
                ("/inventory",),
                fetch_all=True
            )
            if not existing:
                for role in ["Admin", "Production", "Management", "Supervisor", "Team Lead"]:
                    run_query(
                        """
                        INSERT IGNORE INTO role_menu_permissions (role, route, is_allowed)
                        VALUES (%s, %s, 1)
                        """,
                        (role, "/inventory")
                    )

        has_feedback = run_query(
            "SELECT 1 FROM role_menu_permissions WHERE route = %s LIMIT 1",
            ("/feedback",),
            fetch_all=True,
        )
        if not has_feedback:
            for role in ["Admin", "Production", "Management", "Supervisor", "Team Lead"]:
                run_query(
                    """
                    INSERT IGNORE INTO role_menu_permissions (role, route, is_allowed)
                    VALUES (%s, %s, 1)
                    """,
                    (role, "/feedback"),
                )
    except Exception:
        pass


def _is_admin(user):
    role = (user or {}).get("role")
    return isinstance(role, str) and role.strip().lower() == "admin"


def _known_roles():
    seed_options(ROLE_CATEGORY, USER_ROLES)
    rows = run_query(
        """
        SELECT DISTINCT role
        FROM users
        WHERE role IS NOT NULL AND TRIM(role) <> ''
        """,
        fetch_all=True,
    ) or []
    dynamic_roles = {
        (r.get("role") or "").strip()
        for r in rows
        if (r.get("role") or "").strip()
    }
    configured_roles = set(list_options(ROLE_CATEGORY))
    ordered = []
    seen = set()
    for role in USER_ROLES + sorted(configured_roles | dynamic_roles):
        if role and role not in seen:
            ordered.append(role)
            seen.add(role)
    return ordered


def _default_permissions(roles=None):
    roles = roles or _known_roles()
    mapping = {}
    for role in roles:
        if role == "Artist":
            mapping[role] = sorted(_ARTIST_DEFAULTS)
        elif role in ("Supervisor", "Team Lead", "Admin", "Production", "Management"):
            values = set(_FULL_ACCESS_DEFAULTS)
            if role == "Admin":
                values.add("/access-provider")
            mapping[role] = sorted(values)
        else:
            mapping[role] = sorted(_ARTIST_DEFAULTS)
    return mapping


def _validate_permissions(payload):
    if not isinstance(payload, dict):
        return None, "permissions must be an object"

    valid_routes = set(ORDERED_MENU_ROUTES) | set(_ROUTE_ALIASES.keys())
    normalized = {}

    for role, routes in payload.items():
        if not isinstance(role, str) or not role.strip():
            return None, "Role names must be non-empty strings"
        if not isinstance(routes, list):
            return None, f"Routes for role '{role}' must be a list"

        role_routes = []
        for route in routes:
            canonical = _canonical_route(route)
            if canonical not in valid_routes:
                return None, f"Invalid route '{route}' for role '{role}'"
            role_routes.append(canonical)

        normalized[role] = sorted(set(role_routes))

    for role in _known_roles():
        normalized.setdefault(role, [])

    return _apply_route_invariants(normalized), None


def _fetch_permissions():
    rows = run_query(
        """
        SELECT role, route
        FROM role_menu_permissions
        WHERE is_allowed = 1
        ORDER BY role, route
        """,
        fetch_all=True,
    ) or []

    if not rows:
        return _default_permissions()

    role_set = set(_known_roles())
    for row in rows:
        role = (row.get("role") or "").strip()
        if role:
            role_set.add(role)
    mapping = {role: [] for role in sorted(role_set)}
    for row in rows:
        role = row.get("role")
        route = _canonical_route(row.get("route"))
        if role in mapping and route in ORDERED_MENU_ROUTES:
            mapping[role].append(route)

    for role in list(mapping.keys()):
        mapping[role] = sorted(set(mapping.get(role, [])))

    return _apply_route_invariants(mapping)


def _save_permissions(mapping):
    run_query("DELETE FROM role_menu_permissions")
    for role, routes in mapping.items():
        for route in routes:
            canonical = _canonical_route(route)
            run_query(
                """
                INSERT INTO role_menu_permissions (role, route, is_allowed)
                VALUES (%s, %s, 1)
                """,
                (role, canonical),
            )


def _diff_permissions(previous, current):
    changes = []
    all_roles = sorted(set(previous.keys()) | set(current.keys()))
    for role in all_roles:
        prev_routes = set(previous.get(role, []))
        curr_routes = set(current.get(role, []))
        for route in ORDERED_MENU_ROUTES:
            old_allowed = route in prev_routes
            new_allowed = route in curr_routes
            if old_allowed != new_allowed:
                changes.append(
                    {
                        "role": role,
                        "route": route,
                        "oldAllowed": old_allowed,
                        "newAllowed": new_allowed,
                    }
                )
    return changes


def _write_audit(changed_by_user_id, action, changes):
    if not changes:
        return
    for change in changes:
        run_query(
            """
            INSERT INTO role_menu_permission_audit
                (changed_by_user_id, action, role, route, old_allowed, new_allowed)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                changed_by_user_id,
                action,
                change["role"],
                change["route"],
                1 if change["oldAllowed"] else 0,
                1 if change["newAllowed"] else 0,
            ),
        )


def _fetch_audit_logs(limit=200):
    rows = run_query(
        """
        SELECT a.id,
               a.changed_by_user_id,
               u.name AS changed_by_name,
               a.action,
               a.role,
               a.route,
               a.old_allowed,
               a.new_allowed,
               a.changed_at
        FROM role_menu_permission_audit a
        LEFT JOIN users u ON u.user_id = a.changed_by_user_id
        ORDER BY a.changed_at DESC, a.id DESC
        LIMIT %s
        """,
        (limit,),
        fetch_all=True,
    ) or []

    return [
        {
            "id": int(r["id"]),
            "changedByUserId": r["changed_by_user_id"],
            "changedByName": r.get("changed_by_name"),
            "action": r["action"],
            "role": r["role"],
            "route": r["route"],
            "oldAllowed": bool(r["old_allowed"]),
            "newAllowed": bool(r["new_allowed"]),
            "changedAt": r["changed_at"].isoformat() if r.get("changed_at") else None,
        }
        for r in rows
    ]


@access_bp.route("/permissions", methods=["GET"])
@token_required
def get_permissions(current_user_id):
    _ensure_table()
    mapping = _fetch_permissions()
    payload = {
        "permissions": mapping,
        "routes": ORDERED_MENU_ROUTES,
        "roles": sorted(mapping.keys()),
    }
    user = get_user(current_user_id)
    if _is_admin(user):
        payload["logs"] = _fetch_audit_logs()
    return success(payload)


@access_bp.route("/permissions", methods=["PUT"])
@token_required
def update_permissions(current_user_id):
    user = get_user(current_user_id)
    if not _is_admin(user):
        return failure("Only Admin can update access permissions.", 403)

    _ensure_table()

    data = request.get_json(silent=True) or {}
    permissions = data.get("permissions")
    normalized, error = _validate_permissions(permissions)
    if error:
        return failure(error, 400)

    previous = _fetch_permissions()
    changes = _diff_permissions(previous, normalized)
    _save_permissions(normalized)
    _write_audit(current_user_id, "update", changes)
    logs = _fetch_audit_logs()
    return success(
        {
            "permissions": normalized,
            "roles": sorted(normalized.keys()),
            "changes": changes,
            "changedCount": len(changes),
            "logs": logs,
        }
    )


@access_bp.route("/permissions/reset", methods=["POST"])
@token_required
def reset_permissions(current_user_id):
    user = get_user(current_user_id)
    if not _is_admin(user):
        return failure("Only Admin can reset access permissions.", 403)

    _ensure_table()
    previous = _fetch_permissions()
    defaults = _default_permissions()
    changes = _diff_permissions(previous, defaults)
    _save_permissions(defaults)
    _write_audit(current_user_id, "reset", changes)
    logs = _fetch_audit_logs()
    return success(
        {
            "permissions": defaults,
            "roles": sorted(defaults.keys()),
            "changes": changes,
            "changedCount": len(changes),
            "logs": logs,
        }
    )


@access_bp.route("/permissions/audit", methods=["GET"])
@token_required
def permission_audit(current_user_id):
    user = get_user(current_user_id)
    if not _is_admin(user):
        return failure("Only Admin can view access audit logs.", 403)

    _ensure_table()

    try:
        limit = int(request.args.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(limit, 1000))

    return success({"logs": _fetch_audit_logs(limit)})
