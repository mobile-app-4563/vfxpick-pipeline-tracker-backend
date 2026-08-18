-- ============================================================
-- Migration 005: Dedicated Production Management grid table
--
-- The Production Management grid previously read/wrote the SAME
-- `shots` table used by the Projects module (via migration_004's
-- grid columns), so editing the production grid mutated project
-- shot data. This migration gives Production Management its own
-- fully independent table: every 20-column Excel-template field is
-- stored here (client/show names are denormalized so the grid never
-- depends on the shared clients/shows/shots tables).
--
-- The table is seeded from the current shots JOIN so no existing
-- production-grid data is lost.
-- ============================================================

USE vfxpick_pipeline;

SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS production_grid;

SET FOREIGN_KEY_CHECKS = 1;

-- ============================================================
-- TABLE: production_grid
-- One row per production-grid entry (20 Excel-template columns).
-- grid_id is the row key the frontend uses as its "shotId".
-- ============================================================
CREATE TABLE production_grid (
    grid_id             VARCHAR(30)     PRIMARY KEY,
    coordinator         VARCHAR(100)    DEFAULT NULL,
    month               VARCHAR(20)     DEFAULT NULL,
    shots_received_date DATE            DEFAULT NULL,
    client_for_ref      VARCHAR(100)    DEFAULT NULL,
    client_name         VARCHAR(150)    DEFAULT NULL,
    show_name           VARCHAR(200)    DEFAULT NULL,
    wip_eta             DATE            DEFAULT NULL,
    eta                 DATE            DEFAULT NULL,
    shot_code           VARCHAR(50)     DEFAULT NULL,
    frames              INT             DEFAULT 0,
    tasks               VARCHAR(50)     DEFAULT NULL,
    review_notes        TEXT            DEFAULT NULL,
    status              ENUM('Hold', 'Approved', 'Awaiting Approval', 'Approved Internal')
                                        NOT NULL DEFAULT 'Awaiting Approval',
    delivered_on        DATE            DEFAULT NULL,
    work_station        VARCHAR(100)    DEFAULT NULL,
    shot_mandays        DECIMAL(6,2)    DEFAULT 0,
    approved_client_md  DECIMAL(6,2)    DEFAULT 0,
    fl_eta              DATE            DEFAULT NULL,
    fl_mandays          DECIMAL(6,2)    DEFAULT 0,

    created_at          TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    updated_by          VARCHAR(20)     DEFAULT NULL,

    INDEX idx_production_grid_status (status),
    INDEX idx_production_grid_month (month),
    INDEX idx_production_grid_show (show_name),
    INDEX idx_production_grid_shot (shot_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- Seed: copy the current production grid (shots JOIN) into the
-- new dedicated table so switching tables loses no data.
-- ============================================================
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
JOIN clients c ON sh.client_id = c.client_id;
