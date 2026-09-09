-- ============================================================
-- migration_011: department_menu_permissions
-- ------------------------------------------------------------
-- Access Provider is enforced by BOTH role and department.
--   * role_menu_permissions        (role x route)  - already present
--   * department_menu_permissions  (dept x route)  - added here
-- A user's effective menus are the intersection of the menus granted to
-- their ROLE and the menus granted to their DEPARTMENT.
--
-- These tables are also created on demand at backend startup by
-- _ensure_table() in access/routes.py; this migration exists so the
-- schema is versioned and identical on fresh installs (full_setup.sql).
-- Running it against an existing database is safe (IF NOT EXISTS).
-- ============================================================

CREATE TABLE IF NOT EXISTS department_menu_permissions (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    department      VARCHAR(50)     NOT NULL,
    route           VARCHAR(100)    NOT NULL,
    is_allowed      TINYINT(1)      NOT NULL DEFAULT 1,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_department_route (department, route),
    INDEX idx_dept_allowed (department, is_allowed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS department_menu_permission_audit (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    changed_by_user_id  VARCHAR(20)     NOT NULL,
    action              VARCHAR(30)     NOT NULL,      -- update | reset
    department          VARCHAR(50)     NOT NULL,
    route               VARCHAR(100)    NOT NULL,
    old_allowed         TINYINT(1)      NOT NULL,
    new_allowed         TINYINT(1)      NOT NULL,
    changed_at          TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_audit_changed_at (changed_at DESC),
    INDEX idx_audit_actor (changed_by_user_id),
    INDEX idx_audit_dept_route (department, route)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
