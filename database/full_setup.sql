-- ============================================================
-- VFXPick Pipeline - Idempotent Database Setup and Upgrade
-- Run this single file for a new or existing database.
-- Domain: Departments (ROTO, PAINT, MM, COMP) -> Clients -> Shows -> Shots
--
-- This is the single source of truth. It includes:
--   * All shots Excel/import columns (coordinator, total_frames,
--     allocation/starting/complete dates, daily_wip, mandays,
--     approved_*, comments, complexity, from_* cross-dept fields)
--   * chat_messages / attachments / notifications tables
--   * Performance composite indexes on shots (formerly separate
--     migration_perf_indexes.sql)
-- Existing tables, rows, columns, and permissions are preserved.
-- ============================================================

CREATE DATABASE IF NOT EXISTS vfxpick_pipeline
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE vfxpick_pipeline;

SET FOREIGN_KEY_CHECKS = 0;

-- ============================================================
-- TABLE 1: users  (auth + teams + artist assignment)
-- role/department stored as VARCHAR so auth stays flexible.
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    user_id         VARCHAR(20)     PRIMARY KEY,
    name            VARCHAR(100)    NOT NULL,
    email           VARCHAR(150)    NOT NULL UNIQUE,
    department      VARCHAR(50)     NOT NULL,          -- ROTO | PAINT | MM | COMP | Production | Management
    password_hash   VARCHAR(255)    NOT NULL,
    role            VARCHAR(50)     NOT NULL DEFAULT 'Artist', -- Admin|Production|Management|Supervisor|Team Lead|Artist
    level           VARCHAR(20)     DEFAULT NULL,      -- Senior | Mid | Junior (artists only)
    status          ENUM('Active', 'Disabled') NOT NULL DEFAULT 'Active',
    avatar          VARCHAR(10)     DEFAULT '',
    phone           VARCHAR(20)     DEFAULT NULL,
    employee_id_ext VARCHAR(50)     DEFAULT NULL,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_users_email (email),
    INDEX idx_users_department (department),
    INDEX idx_users_role (role)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- TABLE 2: user_settings  (kept so auth /register keeps working)
-- ============================================================
CREATE TABLE IF NOT EXISTS user_settings (
    user_id                 VARCHAR(20)     PRIMARY KEY,
    theme_mode              ENUM('dark', 'light') NOT NULL DEFAULT 'dark',
    email_notifications     BOOLEAN         NOT NULL DEFAULT TRUE,
    push_notifications      BOOLEAN         NOT NULL DEFAULT TRUE,
    updated_at              TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- TABLE 3: clients
-- ============================================================
CREATE TABLE IF NOT EXISTS clients (
    client_id       VARCHAR(20)     PRIMARY KEY,       -- e.g. '108', '110'
    client_name     VARCHAR(150)    NOT NULL,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- TABLE 4: shows
-- ============================================================
CREATE TABLE IF NOT EXISTS shows (
    show_id         VARCHAR(20)     PRIMARY KEY,
    client_id       VARCHAR(20)     NOT NULL,
    show_name       VARCHAR(200)    NOT NULL,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (client_id) REFERENCES clients(client_id) ON DELETE CASCADE,
    INDEX idx_shows_client (client_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- TABLE 5: shots  (the core entity)
-- ============================================================
CREATE TABLE IF NOT EXISTS shots (
    shot_id            VARCHAR(20)  PRIMARY KEY,
    show_id            VARCHAR(20)  NOT NULL,
    department         VARCHAR(20)  NOT NULL,          -- ROTO | PAINT | MM | COMP
    shot_code          VARCHAR(50)  NOT NULL,
    frame_in           INT          DEFAULT 0,
    frame_out          INT          DEFAULT 0,
    total_frames       INT          DEFAULT 0,
    supervisor_bid     DECIMAL(6,2) DEFAULT 0,
    client_bid         DECIMAL(6,2) DEFAULT 0,
    client_eta         DATE         DEFAULT NULL,
    notes              TEXT         DEFAULT NULL,
    status             ENUM('Hold', 'Approved', 'Awaiting Approval', 'Approved Internal')
                                    NOT NULL DEFAULT 'Awaiting Approval',
    artist_id          VARCHAR(20)  DEFAULT NULL,
    level_of_shot      VARCHAR(50)  DEFAULT NULL,
    artist_bid         DECIMAL(6,2) DEFAULT 0,
    artist_eta         DATE         DEFAULT NULL,
    description        TEXT         DEFAULT NULL,
    supervisor_status  ENUM('Feedback', 'Approved', 'Hold')
                                    DEFAULT NULL,
    artist_status      ENUM('YTS', 'In Progress', 'Awaiting QC', 'WIP Completed', 'Render & Upload Completed', 'QC', 'Additional')
                                    NOT NULL DEFAULT 'YTS',
    coordinator        VARCHAR(100) DEFAULT NULL,
    allocated_date     DATE         DEFAULT NULL,
    allocation_date    DATE         DEFAULT NULL,
    allocation_eta     DATE         DEFAULT NULL,
    starting_date      DATE         DEFAULT NULL,
    complete_date      DATE         DEFAULT NULL,
    daily_wip          DECIMAL(5,2) DEFAULT 0,
    mandays            DECIMAL(6,2) NOT NULL DEFAULT 0, -- delivered mandays
    consumed_mandays   DECIMAL(6,2) DEFAULT 0,
    saved_mandays      DECIMAL(6,2) DEFAULT 0,
    approved_version   VARCHAR(50)  DEFAULT NULL,
    approved_by        VARCHAR(100) DEFAULT NULL,
    comments           TEXT         DEFAULT NULL,
    complexity         VARCHAR(50)  DEFAULT NULL,
    priority           VARCHAR(50)  DEFAULT NULL,
    due_date           DATE         DEFAULT NULL,
    client_feedback    TEXT         DEFAULT NULL,
    from_roto          TEXT         DEFAULT NULL,
    from_paint         TEXT         DEFAULT NULL,
    from_mm            TEXT         DEFAULT NULL,
    from_comp          TEXT         DEFAULT NULL,
    created_at         TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    FOREIGN KEY (show_id) REFERENCES shows(show_id) ON DELETE CASCADE,
    FOREIGN KEY (artist_id) REFERENCES users(user_id) ON DELETE SET NULL,

    INDEX idx_shots_show (show_id),
    INDEX idx_shots_department (department),
    INDEX idx_shots_status (status),
    INDEX idx_shots_artist (artist_id),
    INDEX idx_shots_due (due_date),
    -- Performance composite indexes (formerly migration_perf_indexes.sql)
    INDEX idx_shots_show_dept (show_id, department),
    INDEX idx_shots_dept_status (department, status),
    INDEX idx_shots_supervisor_status (supervisor_status),
    INDEX idx_shots_artist_status (artist_status),
    INDEX idx_shots_show_dept_code (show_id, department, shot_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- TABLE 6: chat_messages  (per-shot chat with attachment metadata)
-- ============================================================
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id      VARCHAR(30)     PRIMARY KEY,
    shot_id         VARCHAR(20)     DEFAULT NULL,
    sender_id       VARCHAR(20)     DEFAULT NULL,
    message         TEXT            NOT NULL,
    attachment_name VARCHAR(255)    DEFAULT NULL,
    attachment_url  VARCHAR(500)    DEFAULT NULL,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (shot_id) REFERENCES shots(shot_id) ON DELETE CASCADE,
    FOREIGN KEY (sender_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_chat_shot (shot_id),
    INDEX idx_chat_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- TABLE 7: attachments  (supporting materials / shared documents)
-- ============================================================
CREATE TABLE IF NOT EXISTS attachments (
    attachment_id   VARCHAR(30)     PRIMARY KEY,
    shot_id         VARCHAR(20)     DEFAULT NULL,
    uploaded_by     VARCHAR(20)     DEFAULT NULL,
    file_name       VARCHAR(255)    NOT NULL,
    file_url        VARCHAR(500)    NOT NULL,
    file_type       VARCHAR(50)     DEFAULT NULL,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (shot_id) REFERENCES shots(shot_id) ON DELETE CASCADE,
    FOREIGN KEY (uploaded_by) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_attach_shot (shot_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- TABLE 8: notifications
-- ============================================================
CREATE TABLE IF NOT EXISTS notifications (
    id              VARCHAR(30)     PRIMARY KEY,
    user_id         VARCHAR(20)     DEFAULT NULL,
    message         TEXT            NOT NULL,
    type            VARCHAR(50)     NOT NULL DEFAULT 'System Notification',
    is_read         BOOLEAN         NOT NULL DEFAULT FALSE,
    timestamp       TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_notifications_user (user_id),
    INDEX idx_notifications_read (is_read),
    INDEX idx_notifications_timestamp (timestamp DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- TABLE 9: role_menu_permissions (Access Provider persistence)
-- ============================================================
CREATE TABLE IF NOT EXISTS role_menu_permissions (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    role            VARCHAR(50)     NOT NULL,
    route           VARCHAR(100)    NOT NULL,
    is_allowed      BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_role_route (role, route),
    INDEX idx_role_allowed (role, is_allowed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- TABLE 10: role_menu_permission_audit (permission change history)
-- ============================================================
CREATE TABLE IF NOT EXISTS role_menu_permission_audit (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    changed_by_user_id  VARCHAR(20)     NOT NULL,
    action              VARCHAR(30)     NOT NULL,      -- update | reset
    role                VARCHAR(50)     NOT NULL,
    route               VARCHAR(100)    NOT NULL,
    old_allowed         BOOLEAN         NOT NULL,
    new_allowed         BOOLEAN         NOT NULL,
    changed_at          TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_audit_changed_at (changed_at DESC),
    INDEX idx_audit_actor (changed_by_user_id),
    INDEX idx_audit_role_route (role, route)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- TABLE 10b: department_menu_permissions (Access Provider persistence)
-- Effective menus = role menus INTERSECT department menus, so both
-- permission sets are stored and enforced together.
-- ============================================================
CREATE TABLE IF NOT EXISTS department_menu_permissions (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    department      VARCHAR(50)     NOT NULL,
    route           VARCHAR(100)    NOT NULL,
    is_allowed      BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_department_route (department, route),
    INDEX idx_dept_allowed (department, is_allowed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- TABLE 10c: department_menu_permission_audit (department permission change history)
-- ============================================================
CREATE TABLE IF NOT EXISTS department_menu_permission_audit (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    changed_by_user_id  VARCHAR(20)     NOT NULL,
    action              VARCHAR(30)     NOT NULL,      -- update | reset
    department          VARCHAR(50)     NOT NULL,
    route               VARCHAR(100)    NOT NULL,
    old_allowed         BOOLEAN         NOT NULL,
    new_allowed         BOOLEAN         NOT NULL,
    changed_at          TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_audit_changed_at (changed_at DESC),
    INDEX idx_audit_actor (changed_by_user_id),
    INDEX idx_audit_dept_route (department, route)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- TABLE 11: activity_audit_log (all module changes)
-- ============================================================
CREATE TABLE IF NOT EXISTS activity_audit_log (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    changed_by_user_id  VARCHAR(20)     NOT NULL,
    module              VARCHAR(80)     NOT NULL,
    action              VARCHAR(40)     NOT NULL,
    entity_type         VARCHAR(80)     NOT NULL,
    entity_id           VARCHAR(100)    DEFAULT NULL,
    details             JSON            DEFAULT NULL,
    changed_at          TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_activity_changed_at (changed_at DESC),
    INDEX idx_activity_actor (changed_by_user_id),
    INDEX idx_activity_module (module)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- SEED DATA
-- password for all seed users: password123
-- ============================================================

INSERT IGNORE INTO users (user_id, name, email, department, password_hash, role, level, status, avatar) VALUES
('USR001', 'Admin',           'admin@vfxpick.com',    'Production', '$2b$12$VkM9exoH3DptxS06euqDb.9iDtake4NljaR2WeykTWb0SSMCII4zq', 'Admin',      NULL,     'Active', 'AD'),
('USR002', 'Production Head',  'prod@vfxpick.com',     'Production', '$2b$12$VkM9exoH3DptxS06euqDb.9iDtake4NljaR2WeykTWb0SSMCII4zq', 'Production', NULL,     'Active', 'PH'),
('USR003', 'Roto Supervisor',  'roto.sup@vfxpick.com', 'ROTO',      '$2b$12$VkM9exoH3DptxS06euqDb.9iDtake4NljaR2WeykTWb0SSMCII4zq', 'Supervisor', NULL,     'Active', 'RS'),
('USR004', 'Comp Supervisor',  'comp.sup@vfxpick.com', 'COMP',      '$2b$12$VkM9exoH3DptxS06euqDb.9iDtake4NljaR2WeykTWb0SSMCII4zq', 'Supervisor', NULL,     'Active', 'CS'),
('USR005', 'Sunil Kumar',      'sunil@vfxpick.com',    'ROTO',       '$2b$12$VkM9exoH3DptxS06euqDb.9iDtake4NljaR2WeykTWb0SSMCII4zq', 'Artist',     'Mid',    'Active', 'SK'),
('USR006', 'Suriya',           'suriya@vfxpick.com',   'ROTO',       '$2b$12$VkM9exoH3DptxS06euqDb.9iDtake4NljaR2WeykTWb0SSMCII4zq', 'Artist',     'Senior', 'Active', 'SU'),
('USR007', 'Melvin',           'melvin@vfxpick.com',   'PAINT',      '$2b$12$VkM9exoH3DptxS06euqDb.9iDtake4NljaR2WeykTWb0SSMCII4zq', 'Artist',     'Mid',    'Active', 'ME'),
('USR008', 'Gowthaman',        'gowtham@vfxpick.com',  'COMP',       '$2b$12$VkM9exoH3DptxS06euqDb.9iDtake4NljaR2WeykTWb0SSMCII4zq', 'Artist',     'Senior', 'Active', 'GO'),
('USR009', 'Ravi',             'ravi@vfxpick.com',     'MM',         '$2b$12$VkM9exoH3DptxS06euqDb.9iDtake4NljaR2WeykTWb0SSMCII4zq', 'Artist',     'Junior', 'Active', 'RA'),
('USR010', 'MM Lead',          'mm.lead@vfxpick.com',  'MM',         '$2b$12$VkM9exoH3DptxS06euqDb.9iDtake4NljaR2WeykTWb0SSMCII4zq', 'Team Lead',  NULL,     'Active', 'ML');

INSERT IGNORE INTO user_settings (user_id) VALUES
('USR001'), ('USR002'), ('USR003'), ('USR004'), ('USR005'),
('USR006'), ('USR007'), ('USR008'), ('USR009'), ('USR010');

INSERT IGNORE INTO clients (client_id, client_name) VALUES
('108', 'Client 108'),
('110', 'Client 110'),
('120', 'Client 120'),
('156', 'Client 156'),
('218', 'Client 218');

INSERT IGNORE INTO shows (show_id, client_id, show_name) VALUES
('SHW001', '108', 'Cyber Horizon'),
('SHW002', '110', 'Dragon Realm'),
('SHW003', '120', 'Desert Storm'),
('SHW004', '156', 'Ocean Deep'),
('SHW005', '218', 'Night City');

INSERT IGNORE INTO shots
    (shot_id, show_id, department, shot_code, frame_in, frame_out, total_frames,
     supervisor_bid, client_bid, client_eta, notes, status, artist_id, level_of_shot,
     artist_bid, artist_eta, description, supervisor_status, artist_status, coordinator,
     allocated_date, allocation_date, allocation_eta, starting_date, complete_date,
     daily_wip, mandays, consumed_mandays, saved_mandays, approved_version, approved_by,
     comments, complexity, due_date, client_feedback,
     from_roto, from_paint, from_mm, from_comp)
VALUES
('SHT001', 'SHW001', 'ROTO', 'CYB_010_0010', 1001, 1085, 85,
 2.50, 3.00, DATE_ADD(CURDATE(), INTERVAL 5 DAY),  'Lock roto on hero',      'Approved Internal', 'USR005', 'Medium',
 2.00, DATE_ADD(CURDATE(), INTERVAL 3 DAY), 'Full body roto', 'Feedback', 'In Progress',  NULL,
 DATE_SUB(CURDATE(), INTERVAL 1 DAY), NULL, NULL, NULL, NULL,
 0, 2.00, 0, 0, NULL, NULL,
 NULL, 'Low', DATE_ADD(CURDATE(), INTERVAL 5 DAY),  NULL,
 NULL, NULL, NULL, NULL),
('SHT002', 'SHW001', 'ROTO', 'CYB_010_0020', 1086, 1150, 65,
 1.50, 2.00, DATE_ADD(CURDATE(), INTERVAL 6 DAY),  'Background roto',        'Awaiting Approval', 'USR006', 'Easy',
 1.50, DATE_ADD(CURDATE(), INTERVAL 4 DAY), 'BG plates roto', NULL,          'YTS',          NULL,
 NULL,                                NULL, NULL, NULL, NULL,
 0, 0.00, 0, 0, NULL, NULL,
 NULL, 'Low', DATE_ADD(CURDATE(), INTERVAL 6 DAY),  NULL,
 NULL, NULL, NULL, NULL),
('SHT003', 'SHW001', 'COMP', 'CYB_010_0010', 1001, 1085, 85,
 3.00, 4.00, DATE_ADD(CURDATE(), INTERVAL 8 DAY),  'Final comp neon',        'Hold',              'USR008', 'Hard',
 3.50, DATE_ADD(CURDATE(), INTERVAL 7 DAY), 'Neon comp',      'Hold',        'WIP Completed', NULL,
 DATE_SUB(CURDATE(), INTERVAL 2 DAY), NULL, NULL, NULL, NULL,
 0, 3.50, 0, 0, NULL, NULL,
 NULL, 'High', DATE_ADD(CURDATE(), INTERVAL 8 DAY),  'Push the glow',
 NULL, NULL, NULL, NULL),
('SHT004', 'SHW002', 'PAINT','DRG_001_0010', 2001, 2120, 120,
 2.00, 2.50, DATE_ADD(CURDATE(), INTERVAL 4 DAY),  'Wire removal',           'Approved',          'USR007', 'Medium',
 2.00, DATE_SUB(CURDATE(), INTERVAL 1 DAY), 'Paint cleanup',  'Approved',    'QC',           NULL,
 DATE_SUB(CURDATE(), INTERVAL 3 DAY), NULL, NULL, NULL, NULL,
 0, 2.00, 0, 0, 'v2', 'Ravi',
 'Clean work', 'Low', DATE_ADD(CURDATE(), INTERVAL 4 DAY),  'Looks good',
 NULL, NULL, NULL, NULL),
('SHT005', 'SHW003', 'MM',   'DST_005_0030', 3001, 3090, 90,
 1.00, 1.50, DATE_ADD(CURDATE(), INTERVAL 10 DAY), 'Camera track desert',    'Awaiting Approval', 'USR009', 'Easy',
 1.00, DATE_ADD(CURDATE(), INTERVAL 9 DAY), 'Matchmove cam',  NULL,          'YTS',          NULL,
 NULL,                                NULL, NULL, NULL, NULL,
 0, 0.00, 0, 0, NULL, NULL,
 NULL, 'Low', DATE_ADD(CURDATE(), INTERVAL 10 DAY), NULL,
 NULL, NULL, NULL, NULL),
('SHT006', 'SHW005', 'COMP', 'NGT_002_0050', 5001, 5075, 75,
 2.50, 3.00, DATE_ADD(CURDATE(), INTERVAL 12 DAY), 'Night city integration', 'Approved Internal', NULL, 'Key level shot',
 0.00, NULL,                               'City comp',      NULL,          'YTS',          NULL,
 NULL,                                NULL, NULL, NULL, NULL,
 0, 0.00, 0, 0, NULL, NULL,
 NULL, 'Critical', DATE_ADD(CURDATE(), INTERVAL 12 DAY), NULL,
 NULL, NULL, NULL, NULL);

INSERT IGNORE INTO chat_messages (message_id, shot_id, sender_id, message, created_at) VALUES
('MSG001', 'SHT001', 'USR003', 'Please tighten the matte around the shoulder.', DATE_SUB(NOW(), INTERVAL 3 HOUR)),
('MSG002', 'SHT001', 'USR005', 'On it, will submit for QC by EOD.',             DATE_SUB(NOW(), INTERVAL 2 HOUR));

INSERT IGNORE INTO notifications (id, user_id, message, type, is_read, timestamp) VALUES
('NTF001', 'USR005', 'New shot CYB_010_0010 has been assigned to you.',    'Task Assigned', FALSE, DATE_SUB(NOW(), INTERVAL 2 HOUR)),
('NTF002', 'USR003', 'Shot CYB_010_0010 submitted for QC by Sunil Kumar.', 'QC Submitted',  FALSE, DATE_SUB(NOW(), INTERVAL 1 HOUR));

-- ============================================================
-- ACCESS PROVIDER SEED (current route flow)
-- Admin: full access including Access Provider.
-- Production/Management/Supervisor/Team Lead: full module access minus Access Provider.
-- Artist: restricted menu.
-- ============================================================
INSERT IGNORE INTO role_menu_permissions (role, route, is_allowed) VALUES
-- Admin
('Admin', '/home', TRUE),
('Admin', '/dashboard', TRUE),
('Admin', '/bidding', TRUE),
('Admin', '/projects', TRUE),
('Admin', '/assets', TRUE),
('Admin', '/tasks', TRUE),
('Admin', '/review', TRUE),
('Admin', '/feedback', TRUE),
('Admin', '/reports', TRUE),
('Admin', '/teams', TRUE),
('Admin', '/notifications', TRUE),
('Admin', '/hrms', TRUE),
('Admin', '/inventory', TRUE),
('Admin', '/production-management', TRUE),
('Admin', '/access-provider', TRUE),

-- Production
('Production', '/home', TRUE),
('Production', '/dashboard', TRUE),
('Production', '/bidding', TRUE),
('Production', '/projects', TRUE),
('Production', '/assets', TRUE),
('Production', '/tasks', TRUE),
('Production', '/review', TRUE),
('Production', '/feedback', TRUE),
('Production', '/reports', TRUE),
('Production', '/teams', TRUE),
('Production', '/notifications', TRUE),
('Production', '/hrms', TRUE),
('Production', '/inventory', TRUE),
('Production', '/production-management', TRUE),

-- Management
('Management', '/home', TRUE),
('Management', '/dashboard', TRUE),
('Management', '/bidding', TRUE),
('Management', '/projects', TRUE),
('Management', '/assets', TRUE),
('Management', '/tasks', TRUE),
('Management', '/review', TRUE),
('Management', '/feedback', TRUE),
('Management', '/reports', TRUE),
('Management', '/teams', TRUE),
('Management', '/notifications', TRUE),
('Management', '/hrms', TRUE),
('Management', '/inventory', TRUE),
('Management', '/production-management', TRUE),

-- Supervisor
('Supervisor', '/home', TRUE),
('Supervisor', '/dashboard', TRUE),
('Supervisor', '/bidding', TRUE),
('Supervisor', '/projects', TRUE),
('Supervisor', '/assets', TRUE),
('Supervisor', '/tasks', TRUE),
('Supervisor', '/review', TRUE),
('Supervisor', '/feedback', TRUE),
('Supervisor', '/reports', TRUE),
('Supervisor', '/teams', TRUE),
('Supervisor', '/notifications', TRUE),
('Supervisor', '/hrms', TRUE),
('Supervisor', '/inventory', TRUE),
('Supervisor', '/production-management', TRUE),

-- Team Lead
('Team Lead', '/home', TRUE),
('Team Lead', '/dashboard', TRUE),
('Team Lead', '/bidding', TRUE),
('Team Lead', '/projects', TRUE),
('Team Lead', '/assets', TRUE),
('Team Lead', '/tasks', TRUE),
('Team Lead', '/review', TRUE),
('Team Lead', '/feedback', TRUE),
('Team Lead', '/reports', TRUE),
('Team Lead', '/teams', TRUE),
('Team Lead', '/notifications', TRUE),
('Team Lead', '/hrms', TRUE),
('Team Lead', '/inventory', TRUE),
('Team Lead', '/production-management', TRUE),

-- Artist
('Artist', '/home', TRUE),
('Artist', '/dashboard', TRUE),
('Artist', '/tasks', TRUE),
('Artist', '/notifications', TRUE);

-- ============================================================
-- IDEMPOTENT UPGRADES FOR EXISTING INSTALLATIONS
-- ============================================================

-- Excel columns added to shots by the former migration_004.
SET @sql := IF(NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'shots' AND column_name = 'month'
), 'ALTER TABLE shots ADD COLUMN month VARCHAR(20) DEFAULT NULL AFTER coordinator', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'shots' AND column_name = 'client_for_ref'
), 'ALTER TABLE shots ADD COLUMN client_for_ref VARCHAR(100) DEFAULT NULL AFTER allocated_date', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'shots' AND column_name = 'wip_eta'
), 'ALTER TABLE shots ADD COLUMN wip_eta DATE DEFAULT NULL AFTER client_for_ref', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'shots' AND column_name = 'work_station'
), 'ALTER TABLE shots ADD COLUMN work_station VARCHAR(100) DEFAULT NULL AFTER complete_date', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'shots' AND column_name = 'approved_client_md'
), 'ALTER TABLE shots ADD COLUMN approved_client_md DECIMAL(6,2) DEFAULT 0 AFTER mandays', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'shots' AND column_name = 'fl_eta'
), 'ALTER TABLE shots ADD COLUMN fl_eta DATE DEFAULT NULL AFTER approved_client_md', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'shots' AND column_name = 'fl_mandays'
), 'ALTER TABLE shots ADD COLUMN fl_mandays DECIMAL(6,2) DEFAULT 0 AFTER fl_eta', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

ALTER TABLE shots MODIFY COLUMN department VARCHAR(255) NOT NULL;

SET @sql := IF(NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'shots' AND column_name = 'priority'
), 'ALTER TABLE shots ADD COLUMN priority VARCHAR(50) DEFAULT NULL AFTER complexity', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Production concerns/issues table.
CREATE TABLE IF NOT EXISTS production_data (
    production_id VARCHAR(20) PRIMARY KEY,
    show_id VARCHAR(20) NOT NULL,
    shot_id VARCHAR(20) DEFAULT NULL,
    concern_type VARCHAR(100) DEFAULT NULL,
    concern_description TEXT DEFAULT NULL,
    status ENUM('Open', 'In Progress', 'Resolved', 'On Hold') NOT NULL DEFAULT 'Open',
    priority ENUM('Low', 'Medium', 'High', 'Critical') NOT NULL DEFAULT 'Medium',
    assigned_to VARCHAR(20) DEFAULT NULL,
    reported_by VARCHAR(20) DEFAULT NULL,
    reported_date DATE NOT NULL DEFAULT (CURDATE()),
    due_date DATE DEFAULT NULL,
    resolved_date DATE DEFAULT NULL,
    planned_resolution VARCHAR(500) DEFAULT NULL,
    actual_resolution VARCHAR(500) DEFAULT NULL,
    impact_area VARCHAR(100) DEFAULT NULL,
    estimated_effort DECIMAL(6,2) DEFAULT 0,
    actual_effort DECIMAL(6,2) DEFAULT 0,
    comments TEXT DEFAULT NULL,
    attachments_url VARCHAR(1000) DEFAULT NULL,
    department VARCHAR(50) NOT NULL DEFAULT 'Production',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    updated_by VARCHAR(20) DEFAULT NULL,
    FOREIGN KEY (show_id) REFERENCES shows(show_id) ON DELETE CASCADE,
    FOREIGN KEY (shot_id) REFERENCES shots(shot_id) ON DELETE SET NULL,
    FOREIGN KEY (assigned_to) REFERENCES users(user_id) ON DELETE SET NULL,
    FOREIGN KEY (reported_by) REFERENCES users(user_id) ON DELETE SET NULL,
    FOREIGN KEY (updated_by) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_production_show (show_id),
    INDEX idx_production_shot (shot_id),
    INDEX idx_production_status (status),
    INDEX idx_production_priority (priority),
    INDEX idx_production_assigned (assigned_to),
    INDEX idx_production_dept (department),
    INDEX idx_production_reported (reported_date),
    INDEX idx_production_show_status (show_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Dedicated 20-column production-management grid.
CREATE TABLE IF NOT EXISTS production_grid (
    grid_id VARCHAR(30) PRIMARY KEY,
    coordinator VARCHAR(100) DEFAULT NULL,
    month VARCHAR(20) DEFAULT NULL,
    shots_received_date DATE DEFAULT NULL,
    client_for_ref VARCHAR(100) DEFAULT NULL,
    client_name VARCHAR(150) DEFAULT NULL,
    show_name VARCHAR(200) DEFAULT NULL,
    wip_eta DATE DEFAULT NULL,
    eta DATE DEFAULT NULL,
    shot_code VARCHAR(50) DEFAULT NULL,
    frames INT DEFAULT 0,
    tasks VARCHAR(255) DEFAULT NULL,
    review_notes TEXT DEFAULT NULL,
    source_row_ref VARCHAR(80) DEFAULT NULL,
    status ENUM('Hold', 'Approved', 'Awaiting Approval', 'Approved Internal',
                'Bidding', 'Bids Received', 'WIP', 'Delivered',
                'Awaiting Reference', 'Awaiting Plates', 'Completed', 'RTU',
                'Rough Cost Shared') NOT NULL DEFAULT 'Awaiting Approval',
    delivered_on DATE DEFAULT NULL,
    work_station VARCHAR(100) DEFAULT NULL,
    shot_mandays DECIMAL(6,2) DEFAULT 0,
    approved_client_md DECIMAL(6,2) DEFAULT 0,
    fl_eta DATE DEFAULT NULL,
    fl_mandays DECIMAL(6,2) DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    updated_by VARCHAR(20) DEFAULT NULL,
    INDEX idx_production_grid_status (status),
    INDEX idx_production_grid_month (month),
    INDEX idx_production_grid_show (show_name),
    INDEX idx_production_grid_shot (shot_code),
    INDEX idx_production_grid_source_ref (source_row_ref)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Upgrade older production_grid tables without dropping their data.
SET @sql := IF(NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'production_grid' AND column_name = 'source_row_ref'
), 'ALTER TABLE production_grid ADD COLUMN source_row_ref VARCHAR(80) DEFAULT NULL AFTER review_notes', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

ALTER TABLE production_grid MODIFY COLUMN tasks VARCHAR(255) DEFAULT NULL;
ALTER TABLE production_grid MODIFY COLUMN status ENUM(
    'Hold', 'Approved', 'Awaiting Approval', 'Approved Internal', 'Bidding',
    'Bids Received', 'WIP', 'Delivered', 'Awaiting Reference',
    'Awaiting Plates', 'Completed', 'RTU', 'Rough Cost Shared'
) NOT NULL DEFAULT 'Awaiting Approval';

-- Seed the grid only when it was newly created and is still empty.
INSERT INTO production_grid (
    grid_id, coordinator, month, shots_received_date, client_for_ref,
    client_name, show_name, wip_eta, eta, shot_code, frames, tasks,
    review_notes, status, delivered_on, work_station, shot_mandays,
    approved_client_md, fl_eta, fl_mandays, created_at, updated_at
)
SELECT
    CONCAT('GRID', LPAD(ROW_NUMBER() OVER (ORDER BY s.created_at, s.shot_id), 6, '0')),
    s.coordinator, s.month, s.allocated_date, s.client_for_ref,
    c.client_name, sh.show_name, s.wip_eta, s.client_eta, s.shot_code,
    s.total_frames, s.department, s.comments, s.status, s.complete_date,
    s.work_station, s.mandays, s.approved_client_md, s.fl_eta, s.fl_mandays,
    s.created_at, s.updated_at
FROM shots s
JOIN shows sh ON s.show_id = sh.show_id
JOIN clients c ON sh.client_id = c.client_id
WHERE NOT EXISTS (SELECT 1 FROM production_grid);

-- Every imported spreadsheet line is an independent record. Do not deduplicate
-- by Tasks, Review Notes, or any other business fields: repeated values are
-- valid and must remain visible in the grid. The bulk import endpoint assigns
-- each row its own grid_id and source_row_ref.

-- Recover the day encoded in legacy mmm-yy imports. This is idempotent because
-- repaired values no longer have day 1.
CREATE TABLE IF NOT EXISTS production_grid_date_backup_013 AS
SELECT grid_id, shots_received_date, wip_eta, eta, delivered_on, fl_eta,
       NOW() AS backed_up_at
FROM production_grid;

UPDATE production_grid
SET shots_received_date = CASE
    WHEN shots_received_date IS NULL OR DAY(shots_received_date) <> 1 THEN shots_received_date
    WHEN YEAR(shots_received_date) % 100 BETWEEN 2 AND 31
     AND YEAR(shots_received_date) % 100 <= DAY(LAST_DAY(DATE_FORMAT(shots_received_date, '2000-%m-01')))
     AND NOT (MONTH(shots_received_date) = 2 AND YEAR(shots_received_date) % 100 = 29
              AND DAY(LAST_DAY(CONCAT(YEAR(CURDATE()), '-02-01'))) < 29)
    THEN STR_TO_DATE(CONCAT(YEAR(CURDATE()), '-', LPAD(MONTH(shots_received_date), 2, '0'), '-',
                            LPAD(YEAR(shots_received_date) % 100, 2, '0')), '%Y-%m-%d')
    ELSE shots_received_date END,
    wip_eta = CASE
    WHEN wip_eta IS NULL OR DAY(wip_eta) <> 1 THEN wip_eta
    WHEN YEAR(wip_eta) % 100 BETWEEN 2 AND 31
     AND YEAR(wip_eta) % 100 <= DAY(LAST_DAY(DATE_FORMAT(wip_eta, '2000-%m-01')))
     AND NOT (MONTH(wip_eta) = 2 AND YEAR(wip_eta) % 100 = 29
              AND DAY(LAST_DAY(CONCAT(YEAR(CURDATE()), '-02-01'))) < 29)
    THEN STR_TO_DATE(CONCAT(YEAR(CURDATE()), '-', LPAD(MONTH(wip_eta), 2, '0'), '-',
                            LPAD(YEAR(wip_eta) % 100, 2, '0')), '%Y-%m-%d')
    ELSE wip_eta END,
    eta = CASE
    WHEN eta IS NULL OR DAY(eta) <> 1 THEN eta
    WHEN YEAR(eta) % 100 BETWEEN 2 AND 31
     AND YEAR(eta) % 100 <= DAY(LAST_DAY(DATE_FORMAT(eta, '2000-%m-01')))
     AND NOT (MONTH(eta) = 2 AND YEAR(eta) % 100 = 29
              AND DAY(LAST_DAY(CONCAT(YEAR(CURDATE()), '-02-01'))) < 29)
    THEN STR_TO_DATE(CONCAT(YEAR(CURDATE()), '-', LPAD(MONTH(eta), 2, '0'), '-',
                            LPAD(YEAR(eta) % 100, 2, '0')), '%Y-%m-%d')
    ELSE eta END,
    delivered_on = CASE
    WHEN delivered_on IS NULL OR DAY(delivered_on) <> 1 THEN delivered_on
    WHEN YEAR(delivered_on) % 100 BETWEEN 2 AND 31
     AND YEAR(delivered_on) % 100 <= DAY(LAST_DAY(DATE_FORMAT(delivered_on, '2000-%m-01')))
     AND NOT (MONTH(delivered_on) = 2 AND YEAR(delivered_on) % 100 = 29
              AND DAY(LAST_DAY(CONCAT(YEAR(CURDATE()), '-02-01'))) < 29)
    THEN STR_TO_DATE(CONCAT(YEAR(CURDATE()), '-', LPAD(MONTH(delivered_on), 2, '0'), '-',
                            LPAD(YEAR(delivered_on) % 100, 2, '0')), '%Y-%m-%d')
    ELSE delivered_on END,
    fl_eta = CASE
    WHEN fl_eta IS NULL OR DAY(fl_eta) <> 1 THEN fl_eta
    WHEN YEAR(fl_eta) % 100 BETWEEN 2 AND 31
     AND YEAR(fl_eta) % 100 <= DAY(LAST_DAY(DATE_FORMAT(fl_eta, '2000-%m-01')))
     AND NOT (MONTH(fl_eta) = 2 AND YEAR(fl_eta) % 100 = 29
              AND DAY(LAST_DAY(CONCAT(YEAR(CURDATE()), '-02-01'))) < 29)
    THEN STR_TO_DATE(CONCAT(YEAR(CURDATE()), '-', LPAD(MONTH(fl_eta), 2, '0'), '-',
                            LPAD(YEAR(fl_eta) % 100, 2, '0')), '%Y-%m-%d')
    ELSE fl_eta END
WHERE DAY(shots_received_date) = 1 OR DAY(wip_eta) = 1 OR DAY(eta) = 1
   OR DAY(delivered_on) = 1 OR DAY(fl_eta) = 1;

-- Repair legacy zero dates without touching real dates.
UPDATE production_grid SET wip_eta = NULL WHERE wip_eta < '1900-01-01';
UPDATE production_grid SET delivered_on = NULL WHERE delivered_on < '1900-01-01';
UPDATE production_grid SET fl_eta = NULL WHERE fl_eta < '1900-01-01';
UPDATE shots SET starting_date = NULL WHERE starting_date < '1900-01-01';

-- Restore the production-management permissions without changing explicit denies.
INSERT IGNORE INTO role_menu_permissions (role, route, is_allowed) VALUES
    ('Admin', '/production-management', TRUE),
    ('Production', '/production-management', TRUE),
    ('Management', '/production-management', TRUE),
    ('Supervisor', '/production-management', TRUE),
    ('Team Lead', '/production-management', TRUE);

SET FOREIGN_KEY_CHECKS = 1;
