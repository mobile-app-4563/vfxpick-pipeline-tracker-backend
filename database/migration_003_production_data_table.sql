-- ============================================================
-- Migration 003: Add production_data table for Production Department
-- This table stores production concerns/issues that are distinct from
-- the actual shot data. Used by Production Department for tracking
-- and management purposes.
-- ============================================================

USE vfxpick_pipeline;

SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS production_data;

SET FOREIGN_KEY_CHECKS = 1;

-- ============================================================
-- TABLE: production_data
-- For Production Department to track and manage production
-- concerns/issues with editable cells following the same pattern
-- as projects and tasks modules.
-- ============================================================
CREATE TABLE production_data (
    production_id       VARCHAR(20)     PRIMARY KEY,
    show_id             VARCHAR(20)     NOT NULL,
    shot_id             VARCHAR(20)     DEFAULT NULL,
    concern_type        VARCHAR(100)    DEFAULT NULL,          -- Type of concern (e.g., Delay, Quality, Resource)
    concern_description TEXT            DEFAULT NULL,           -- Detailed description
    status              ENUM('Open', 'In Progress', 'Resolved', 'On Hold')
                                        NOT NULL DEFAULT 'Open',
    priority            ENUM('Low', 'Medium', 'High', 'Critical')
                                        NOT NULL DEFAULT 'Medium',
    assigned_to         VARCHAR(20)     DEFAULT NULL,           -- User ID of assignee
    reported_by         VARCHAR(20)     DEFAULT NULL,           -- User ID who reported the concern
    
    -- Tracking dates
    reported_date       DATE            NOT NULL DEFAULT CURDATE(),
    due_date            DATE            DEFAULT NULL,
    resolved_date       DATE            DEFAULT NULL,
    
    -- Editable fields (similar to projects/tasks pattern)
    planned_resolution  VARCHAR(500)    DEFAULT NULL,
    actual_resolution   VARCHAR(500)    DEFAULT NULL,
    impact_area         VARCHAR(100)    DEFAULT NULL,           -- Budget, Schedule, Quality, Resources
    estimated_effort    DECIMAL(6,2)    DEFAULT 0,              -- Estimated mandays/hours to resolve
    actual_effort       DECIMAL(6,2)    DEFAULT 0,              -- Actual effort spent
    
    -- Supporting info
    comments            TEXT            DEFAULT NULL,
    attachments_url     VARCHAR(1000)   DEFAULT NULL,           -- Comma-separated URLs
    department          VARCHAR(50)     NOT NULL DEFAULT 'Production', -- For filtering
    
    created_at          TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    updated_by          VARCHAR(20)     DEFAULT NULL,

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

-- Insert sample production concerns for demonstration
INSERT INTO production_data 
    (production_id, show_id, shot_id, concern_type, concern_description, status, priority, 
     assigned_to, reported_by, reported_date, due_date, planned_resolution, impact_area)
VALUES
('PROD001', 'SHW001', 'SHT001', 'Schedule Delay', 'Roto work on CYB_010_0010 delayed by 2 days', 
 'In Progress', 'High', 'USR002', 'USR002', DATE_SUB(CURDATE(), INTERVAL 1 DAY), 
 DATE_ADD(CURDATE(), INTERVAL 2 DAY), 'Allocate additional resources', 'Schedule'),

('PROD002', 'SHW001', NULL, 'Resource Shortage', 'Need additional comp artists for next phase', 
 'Open', 'High', 'USR002', 'USR002', CURDATE(), 
 DATE_ADD(CURDATE(), INTERVAL 5 DAY), 'Hire contract resources', 'Resources'),

('PROD003', 'SHW002', 'SHT004', 'Quality Issue', 'Paint work needs refinement on DRG_001_0010', 
 'Resolved', 'Medium', 'USR007', 'USR003', DATE_SUB(CURDATE(), INTERVAL 3 DAY), 
 CURDATE(), 'Additional review and revision', 'Quality');
