-- ============================================================
-- Performance Index Migration
-- Run this after full_setup.sql to add missing composite
-- indexes that dramatically speed up common API queries.
--
-- NOTE: If an index already exists, the statement will fail with
-- "Duplicate key name" — this is safe to ignore.
-- All statements are independent; a failure on one won't block
-- the rest.
--
-- MariaDB users: replace CREATE INDEX with
--   CREATE INDEX IF NOT EXISTS
-- ============================================================

-- ----------------------------------------------------------
-- 1. shots: (show_id, department) composite
--    Used by: SHOT_SELECT in every shot listing query.
--    Every /projects/shots call filters on these two columns.
-- ----------------------------------------------------------
CREATE INDEX idx_shots_show_dept ON shots (show_id, department);

-- ----------------------------------------------------------
-- 2. shots: (department, status) composite
--    Used by: dashboard/summary GROUP BY, shot listing filters.
-- ----------------------------------------------------------
CREATE INDEX idx_shots_dept_status ON shots (department, status);

-- ----------------------------------------------------------
-- 3. shots: supervisor_status
--    Used by: /options endpoint SELECT DISTINCT supervisor_status
-- ----------------------------------------------------------
CREATE INDEX idx_shots_supervisor_status ON shots (supervisor_status);

-- ----------------------------------------------------------
-- 4. shots: artist_status
--    Used by: /options endpoint SELECT DISTINCT artist_status
-- ----------------------------------------------------------
CREATE INDEX idx_shots_artist_status ON shots (artist_status);

-- ----------------------------------------------------------
-- 5. shots: (show_id, department, shot_code) composite
--    Used by: bulk import upsert lookup
--    (find existing shot by show + department + shot_code)
--    This is the most impactful index for large bulk imports.
-- ----------------------------------------------------------
CREATE INDEX idx_shots_show_dept_code ON shots (show_id, department, shot_code);
