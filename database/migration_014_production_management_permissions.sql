-- Migration 014: restore the missing production-management role permissions.
--
-- The access seed documents these roles as having full module access, but the
-- original seed omitted /production-management. Insert only missing rows so an
-- administrator's explicit deny remains unchanged.

INSERT IGNORE INTO role_menu_permissions (role, route, is_allowed) VALUES
    ('Admin', '/production-management', TRUE),
    ('Production', '/production-management', TRUE),
    ('Management', '/production-management', TRUE),
    ('Supervisor', '/production-management', TRUE),
    ('Team Lead', '/production-management', TRUE);