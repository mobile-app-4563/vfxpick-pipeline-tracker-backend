from flask import Blueprint, request

from auth.middleware import token_required
from common.constants import USER_DEPARTMENTS, USER_ROLES
from common.audit import ensure_activity_table, fetch_activity_logs
from common.db_utils import get_user, run_query
from common.http import failure, success
from common.options_store import (
    DEPARTMENT_CATEGORY,
    ROLE_CATEGORY,
    list_options,
    seed_options,
)

access_bp = Blueprint("access", __name__)

ORDERED_MENU_ROUTES = [
    "/home",
    "/dashboard",
    "/bidding",
    "/projects",
    "/production-management",
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
    "/profile",
    "/audit-logs",
]

_ARTIST_DEFAULTS = {
    "/home",
    "/dashboard",
    "/tasks",
    "/notifications",
    "/profile",
}

_FULL_ACCESS_DEFAULTS = {
    "/home",
    "/dashboard",
    "/bidding",
    "/projects",
    "/production-management",
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
    "/profile",
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
        # /profile is a personal menu every user needs — never revocable.
        route_set.add("/profile")
        if role in _FULL_ACCESS_ROLES:
            route_set.add("/hrms")
        if role == "Admin":
            route_set.update(ORDERED_MENU_ROUTES)
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
    run_query(
        """
        CREATE TABLE IF NOT EXISTS department_menu_permissions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            department VARCHAR(50) NOT NULL,
            route VARCHAR(100) NOT NULL,
            is_allowed TINYINT(1) NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_department_route (department, route),
            INDEX idx_dept_allowed (department, is_allowed)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    ensure_activity_table()
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
    run_query(
        """
        CREATE TABLE IF NOT EXISTS department_menu_permission_audit (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            changed_by_user_id VARCHAR(20) NOT NULL,
            action VARCHAR(30) NOT NULL,
            department VARCHAR(50) NOT NULL,
            route VARCHAR(100) NOT NULL,
            old_allowed TINYINT(1) NOT NULL,
            new_allowed TINYINT(1) NOT NULL,
            changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_audit_changed_at (changed_at DESC),
            INDEX idx_audit_actor (changed_by_user_id),
            INDEX idx_audit_dept_route (department, route)
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

        has_production_management = run_query(
            "SELECT 1 FROM role_menu_permissions WHERE route = %s LIMIT 1",
            ("/production-management",),
            fetch_all=True,
        )
        if not has_production_management:
            for role in ["Admin", "Production", "Management", "Supervisor", "Team Lead"]:
                run_query(
                    """
                    INSERT IGNORE INTO role_menu_permissions (role, route, is_allowed)
                    VALUES (%s, %s, 1)
                    """,
                    (role, "/production-management"),
                )
    except Exception:
        pass


_DELETE_ENABLED_KEY = "delete_options_enabled"
_DELETE_DEPT_PREFIX = "delete_enabled_"


def _ensure_settings_table():
    run_query(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            setting_key VARCHAR(64) PRIMARY KEY,
            setting_value VARCHAR(255) NOT NULL,
            updated_by_user_id VARCHAR(20),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def _dept_delete_key(department):
    """Settings key for one department's delete switch (case-insensitive)."""
    return f"{_DELETE_DEPT_PREFIX}{(department or '').strip().upper()}"


def _known_departments():
    """Ordered list of every department an admin can toggle delete for:
    fixed pipeline departments + registration options + any department that
    already exists on a user row (so brand-new departments show up too)."""
    try:
        rows = run_query(
            "SELECT DISTINCT department FROM users "
            "WHERE department IS NOT NULL AND TRIM(department) <> ''",
            fetch_all=True,
        ) or []
    except Exception:
        rows = []
    dynamic = {
        (r.get("department") or "").strip()
        for r in rows
        if (r.get("department") or "").strip()
    }
    ordered = []
    seen = set()
    for dept in (
        list(USER_DEPARTMENTS)
        + list(list_options(DEPARTMENT_CATEGORY) or [])
        + sorted(dynamic)
    ):
        dept = (dept or "").strip()
        if dept and dept not in seen:
            ordered.append(dept)
            seen.add(dept)
    return ordered


def _get_delete_enabled(department=None):
    """Delete kill-switch, optionally per department.

    With a department, the per-department switch wins (falling back to the
    legacy global value when that department has no explicit row yet).
    Without a department, returns the legacy global value. Anything except
    "0" counts as enabled.
    """
    try:
        _ensure_settings_table()
        if department and (department or "").strip():
            rows = run_query(
                "SELECT setting_value FROM app_settings WHERE setting_key = %s",
                (_dept_delete_key(department),),
                fetch_all=True,
            )
            if rows:
                return rows[0].get("setting_value") != "0"
        rows = run_query(
            "SELECT setting_value FROM app_settings WHERE setting_key = %s",
            (_DELETE_ENABLED_KEY,),
            fetch_all=True,
        )
        if rows and rows[0].get("setting_value") == "0":
            return False
    except Exception:
        pass
    return True


def _department_delete_map():
    """{department: bool} for every known department."""
    return {
        dept: _get_delete_enabled(department=dept)
        for dept in _known_departments()
    }


def _set_delete_enabled(user_id, enabled, department=None):
    _ensure_settings_table()
    if department and (department or "").strip():
        key = _dept_delete_key(department)
    else:
        key = _DELETE_ENABLED_KEY
    run_query(
        """
        INSERT INTO app_settings (setting_key, setting_value, updated_by_user_id)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            setting_value = VALUES(setting_value),
            updated_by_user_id = VALUES(updated_by_user_id)
        """,
        (key, "1" if enabled else "0", user_id),
    )


def delete_enabled_for_user(user):
    """True when the user's own department has delete enabled. Used by the
    DELETE endpoints so the per-department switch is enforced server-side too.
    Falls back to the legacy global kill-switch for users without a
    department."""
    if not user:
        return False
    department = (user.get("department") or "").strip()
    return _get_delete_enabled(department=department or None)


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
                values.update(ORDERED_MENU_ROUTES)
            mapping[role] = sorted(values)
        else:
            # New/unknown roles (e.g. a user registered with a brand-new role,
            # often alongside a new department) get EVERY menu by default so
            # the new user is never locked out, no matter the department.  The
            # admin can fine-tune from the Access Provider screen.
            mapping[role] = sorted(ORDERED_MENU_ROUTES)
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

    # Roles missing from the payload (e.g. a user registered with a new role
    # after the admin loaded the screen) get the same defaults as the empty
    # table path — EVERY menu for unconfigured roles — instead of being wiped
    # down to only /profile.
    defaults = _default_permissions(roles=_known_roles())
    for role in _known_roles():
        if role not in normalized:
            normalized[role] = defaults.get(role, sorted(ORDERED_MENU_ROUTES))

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

    # Roles that exist (e.g. a brand-new role a user registered with, often
    # alongside a new department) but have NO configured permission rows get
    # the same defaults used when the table is empty — EVERY menu — so the
    # new user is never locked out regardless of department.
    defaults = _default_permissions(roles=list(mapping.keys()))
    for role in list(mapping.keys()):
        if not mapping[role]:
            mapping[role] = defaults.get(role, sorted(ORDERED_MENU_ROUTES))

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


# ─────────────────────────────────────────────────────────────────────────────
# Per-department menu permissions
#
# A department's menu switches work like the delete options: they default to
# ALLOWED for every menu, and the admin can turn a menu OFF for a whole
# department.  A user's effective menus are the INTERSECTION of their role
# menus and their department's menus, so a brand-new department (no rows yet)
# leaves users on their role defaults — exactly what we want when a user is
# registered with a new department.
# ─────────────────────────────────────────────────────────────────────────────


def _department_menu_defaults():
    """Every known department allows every menu by default, so new
    departments never lock their users out and role defaults apply."""
    return {dept: list(ORDERED_MENU_ROUTES) for dept in _known_departments()}


def _apply_department_route_invariants(mapping):
    for dept, routes in list(mapping.items()):
        route_set = set(routes or [])
        # /home and /profile are personal menus every user needs.
        route_set.add("/home")
        route_set.add("/profile")
        mapping[dept] = sorted(route_set)
    return mapping


def _fetch_department_menu_permissions():
    """{department: [allowed routes]} for every known department.  Departments
    without any rows (e.g. brand-new departments) default to every menu."""
    rows = run_query(
        """
        SELECT department, route
        FROM department_menu_permissions
        WHERE is_allowed = 1
        ORDER BY department, route
        """,
        fetch_all=True,
    ) or []

    dept_set = set(_known_departments())
    for row in rows:
        dept = (row.get("department") or "").strip()
        if dept:
            dept_set.add(dept)
    mapping = {dept: [] for dept in sorted(dept_set)}
    for row in rows:
        dept = (row.get("department") or "").strip()
        route = _canonical_route(row.get("route"))
        if dept in mapping and route in ORDERED_MENU_ROUTES:
            mapping[dept].append(route)

    defaults = _department_menu_defaults()
    for dept in list(mapping.keys()):
        if not mapping[dept]:
            mapping[dept] = defaults.get(dept, list(ORDERED_MENU_ROUTES))
        mapping[dept] = sorted(set(mapping[dept]))

    return _apply_department_route_invariants(mapping)


def _validate_department_menu_permissions(payload):
    if not isinstance(payload, dict):
        return None, "departmentMenus must be an object"

    valid_routes = set(ORDERED_MENU_ROUTES) | set(_ROUTE_ALIASES.keys())
    normalized = {}

    for dept, routes in payload.items():
        if not isinstance(dept, str) or not dept.strip():
            return None, "Department names must be non-empty strings"
        if not isinstance(routes, list):
            return None, f"Routes for department '{dept}' must be a list"

        dept_routes = []
        for route in routes:
            canonical = _canonical_route(route)
            if canonical not in valid_routes:
                return None, f"Invalid route '{route}' for department '{dept}'"
            dept_routes.append(canonical)

        normalized[dept] = sorted(set(dept_routes))

    # Departments missing from the payload (e.g. a new department registered
    # after the admin loaded the screen) get every menu — role defaults apply
    # to their users, no lockouts.
    defaults = _department_menu_defaults()
    for dept in _known_departments():
        if dept not in normalized:
            normalized[dept] = defaults.get(dept, list(ORDERED_MENU_ROUTES))

    return _apply_department_route_invariants(normalized), None


def _save_department_menu_permissions(mapping):
    run_query("DELETE FROM department_menu_permissions")
    for dept, routes in mapping.items():
        for route in routes:
            canonical = _canonical_route(route)
            run_query(
                """
                INSERT INTO department_menu_permissions (department, route, is_allowed)
                VALUES (%s, %s, 1)
                """,
                (dept, canonical),
            )


def _diff_department_menu_permissions(previous, current):
    changes = []
    all_depts = sorted(set(previous.keys()) | set(current.keys()))
    for dept in all_depts:
        prev_routes = set(previous.get(dept, []))
        curr_routes = set(current.get(dept, []))
        for route in ORDERED_MENU_ROUTES:
            old_allowed = route in prev_routes
            new_allowed = route in curr_routes
            if old_allowed != new_allowed:
                changes.append(
                    {
                        "department": dept,
                        "route": route,
                        "oldAllowed": old_allowed,
                        "newAllowed": new_allowed,
                    }
                )
    return changes


def _write_department_audit(changed_by_user_id, action, changes):
    if not changes:
        return
    for change in changes:
        run_query(
            """
            INSERT INTO department_menu_permission_audit
                (changed_by_user_id, action, department, route, old_allowed, new_allowed)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                changed_by_user_id,
                action,
                change["department"],
                change["route"],
                1 if change["oldAllowed"] else 0,
                1 if change["newAllowed"] else 0,
            ),
        )


def _fetch_department_audit_logs(limit=200):
    rows = run_query(
        """
        SELECT a.id,
               a.changed_by_user_id,
               u.name AS changed_by_name,
               a.action,
               u.email AS changed_by_username,
               a.department,
               a.route,
               a.old_allowed,
               a.new_allowed,
               a.changed_at
        FROM department_menu_permission_audit a
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
            "changedByUsername": r.get("changed_by_username"),
            "action": r["action"],
            "role": r.get("department"),
            "route": r["route"],
            "oldAllowed": bool(r["old_allowed"]),
            "newAllowed": bool(r["new_allowed"]),
            "changedAt": r["changed_at"].isoformat() if r.get("changed_at") else None,
            "module": "Access Provider",
            "entityType": "Department Menu Permission",
            "entityId": r["route"],
            "details": {
                "department": r.get("department"),
                "oldAllowed": bool(r["old_allowed"]),
                "newAllowed": bool(r["new_allowed"]),
            },
        }
        for r in rows
    ]


def _fetch_all_department_audit_logs(limit=500):
    logs = _fetch_department_audit_logs(limit=limit)
    logs.sort(key=lambda row: row.get("changedAt") or "", reverse=True)
    return logs[:limit]


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
               u.email AS changed_by_username,
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
            "changedByUsername": r.get("changed_by_username"),
            "action": r["action"],
            "role": r["role"],
            "route": r["route"],
            "oldAllowed": bool(r["old_allowed"]),
            "newAllowed": bool(r["new_allowed"]),
            "changedAt": r["changed_at"].isoformat() if r.get("changed_at") else None,
            "module": "Access Provider",
            "entityType": "Menu Permission",
            "entityId": r["route"],
            "details": {
                "role": r["role"],
                "oldAllowed": bool(r["old_allowed"]),
                "newAllowed": bool(r["new_allowed"]),
            },
        }
        for r in rows
    ]


def _fetch_all_audit_logs(limit=500):
    logs = _fetch_audit_logs(limit=limit)
    logs.extend(_fetch_department_audit_logs(limit=limit))
    logs.extend(fetch_activity_logs(limit=limit))
    logs.sort(key=lambda row: row.get("changedAt") or "", reverse=True)
    return logs[:limit]


@access_bp.route("/permissions", methods=["GET"])
@token_required
def get_permissions(current_user_id):
    _ensure_table()
    mapping = _fetch_permissions()
    payload = {
        "permissions": mapping,
        "routes": ORDERED_MENU_ROUTES,
        "roles": sorted(mapping.keys()),
        "deleteEnabled": _get_delete_enabled(),
        "departments": _department_delete_map(),
        "departmentMenus": _fetch_department_menu_permissions(),
    }
    user = get_user(current_user_id)
    if _is_admin(user):
        payload["logs"] = _fetch_all_audit_logs()
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

    # Department menu switches ride along on the same payload.
    raw_dept_menus = data.get("departmentMenus")
    dept_normalized = None
    dept_changes = []
    if raw_dept_menus is not None:
        dept_normalized, dept_error = _validate_department_menu_permissions(
            raw_dept_menus
        )
        if dept_error:
            return failure(dept_error, 400)
        previous_dept = _fetch_department_menu_permissions()
        dept_changes = _diff_department_menu_permissions(
            previous_dept, dept_normalized
        )
        _save_department_menu_permissions(dept_normalized)
        _write_department_audit(current_user_id, "update", dept_changes)

    logs = _fetch_all_audit_logs()
    return success(
        {
            "permissions": normalized,
            "roles": sorted(normalized.keys()),
            "changes": changes,
            "changedCount": len(changes),
            "departmentMenus": dept_normalized
            or _fetch_department_menu_permissions(),
            "logs": logs,
        }
    )


@access_bp.route("/settings", methods=["GET"])
@token_required
def get_access_settings(current_user_id):
    """Read access feature flags: the legacy global delete kill-switch plus
    the per-department delete map."""
    _ensure_settings_table()
    return success(
        {
            "deleteEnabled": _get_delete_enabled(),
            "departments": _department_delete_map(),
        }
    )


@access_bp.route("/settings", methods=["PUT"])
@token_required
def update_access_settings(current_user_id):
    user = get_user(current_user_id)
    if not _is_admin(user):
        return failure("Only Admin can update access settings.", 403)

    _ensure_settings_table()
    data = request.get_json(silent=True) or {}
    raw = data.get("deleteEnabled")
    if not isinstance(raw, bool):
        return failure("'deleteEnabled' must be a boolean.", 400)

    department = (data.get("department") or "").strip() or None
    _set_delete_enabled(current_user_id, raw, department=department)
    return success(
        {
            "deleteEnabled": _get_delete_enabled(),
            "departments": _department_delete_map(),
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

    previous_dept = _fetch_department_menu_permissions()
    dept_defaults = _department_menu_defaults()
    dept_defaults = _apply_department_route_invariants(dept_defaults)
    dept_changes = _diff_department_menu_permissions(previous_dept, dept_defaults)
    _save_department_menu_permissions(dept_defaults)
    _write_department_audit(current_user_id, "reset", dept_changes)

    logs = _fetch_all_audit_logs()
    return success(
        {
            "permissions": defaults,
            "roles": sorted(defaults.keys()),
            "changes": changes,
            "changedCount": len(changes),
            "departmentMenus": dept_defaults,
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

    return success({"logs": _fetch_all_audit_logs(limit)})
