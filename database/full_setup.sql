-- ============================================================
-- VFXPick Pipeline Restructure - Full Setup (Database + Schema + Seed)
-- Run this file once in MySQL to create everything end-to-end.
-- Domain: Departments (ROTO, PAINT, MM, COMP) -> Clients -> Shows -> Shots
--
-- This is the single source of truth. It includes:
--   * All shots Excel/import columns (coordinator, total_frames,
--     allocation/starting/complete dates, daily_wip, mandays,
--     approved_*, comments, complexity, from_* cross-dept fields)
--   * chat_messages / attachments / notifications tables
--   * Performance composite indexes on shots (formerly separate
--     migration_perf_indexes.sql)
-- The old standalone migrations (migration_001_add_excel_fields.sql,
-- migration_002_ensure_content_tables.sql, migration_perf_indexes.sql)
-- are consolidated here and should no longer be needed.
-- ============================================================

CREATE DATABASE IF NOT EXISTS vfxpick_pipeline
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE vfxpick_pipeline;

SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS attachments;
DROP TABLE IF EXISTS chat_messages;
DROP TABLE IF EXISTS notifications;
DROP TABLE IF EXISTS role_menu_permissions;
DROP TABLE IF EXISTS role_menu_permission_audit;
DROP TABLE IF EXISTS activity_audit_log;
DROP TABLE IF EXISTS user_settings;
DROP TABLE IF EXISTS shots;
DROP TABLE IF EXISTS shows;
DROP TABLE IF EXISTS clients;
DROP TABLE IF EXISTS tasks;
DROP TABLE IF EXISTS projects;
DROP TABLE IF EXISTS departments;
DROP TABLE IF EXISTS project_department_mapping;
DROP TABLE IF EXISTS users;

SET FOREIGN_KEY_CHECKS = 1;

-- ============================================================
-- TABLE 1: users  (auth + teams + artist assignment)
-- role/department stored as VARCHAR so auth stays flexible.
-- ============================================================
CREATE TABLE users (
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
CREATE TABLE user_settings (
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
CREATE TABLE clients (
    client_id       VARCHAR(20)     PRIMARY KEY,       -- e.g. '108', '110'
    client_name     VARCHAR(150)    NOT NULL,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- TABLE 4: shows
-- ============================================================
CREATE TABLE shows (
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
CREATE TABLE shots (
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
CREATE TABLE chat_messages (
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
CREATE TABLE attachments (
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
CREATE TABLE notifications (
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
CREATE TABLE role_menu_permissions (
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
CREATE TABLE role_menu_permission_audit (
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
-- TABLE 11: activity_audit_log (all module changes)
-- ============================================================
CREATE TABLE activity_audit_log (
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

INSERT INTO users (user_id, name, email, department, password_hash, role, level, status, avatar) VALUES
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

INSERT INTO user_settings (user_id) VALUES
('USR001'), ('USR002'), ('USR003'), ('USR004'), ('USR005'),
('USR006'), ('USR007'), ('USR008'), ('USR009'), ('USR010');

INSERT INTO clients (client_id, client_name) VALUES
('108', 'Client 108'),
('110', 'Client 110'),
('120', 'Client 120'),
('156', 'Client 156'),
('218', 'Client 218');

INSERT INTO shows (show_id, client_id, show_name) VALUES
('SHW001', '108', 'Cyber Horizon'),
('SHW002', '110', 'Dragon Realm'),
('SHW003', '120', 'Desert Storm'),
('SHW004', '156', 'Ocean Deep'),
('SHW005', '218', 'Night City');

INSERT INTO shots
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

INSERT INTO chat_messages (message_id, shot_id, sender_id, message, created_at) VALUES
('MSG001', 'SHT001', 'USR003', 'Please tighten the matte around the shoulder.', DATE_SUB(NOW(), INTERVAL 3 HOUR)),
('MSG002', 'SHT001', 'USR005', 'On it, will submit for QC by EOD.',             DATE_SUB(NOW(), INTERVAL 2 HOUR));

INSERT INTO notifications (id, user_id, message, type, is_read, timestamp) VALUES
('NTF001', 'USR005', 'New shot CYB_010_0010 has been assigned to you.',    'Task Assigned', FALSE, DATE_SUB(NOW(), INTERVAL 2 HOUR)),
('NTF002', 'USR003', 'Shot CYB_010_0010 submitted for QC by Sunil Kumar.', 'QC Submitted',  FALSE, DATE_SUB(NOW(), INTERVAL 1 HOUR));

-- ============================================================
-- ACCESS PROVIDER SEED (current route flow)
-- Admin: full access including Access Provider.
-- Production/Management/Supervisor/Team Lead: full module access minus Access Provider.
-- Artist: restricted menu.
-- ============================================================
INSERT INTO role_menu_permissions (role, route, is_allowed) VALUES
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

-- Artist
('Artist', '/home', TRUE),
('Artist', '/dashboard', TRUE),
('Artist', '/tasks', TRUE),
('Artist', '/notifications', TRUE);
