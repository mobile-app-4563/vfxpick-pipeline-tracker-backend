-- Migration 006: add PRIORITY column to shots table
-- Run manually against existing installs:  mysql -u root vfxpick_pipeline < migration_006_shot_priority_column.sql
-- Values stored as plain strings: 'Priority 1', 'Priority 2', 'Priority 3' (NULL when unset)
-- NOTE: uses ADD COLUMN IF NOT EXISTS (MariaDB 10.0.2+) so re-running is safe.
--       For MySQL 8.0 (no IF NOT EXISTS support), drop the IF NOT EXISTS clause.

ALTER TABLE shots
    ADD COLUMN IF NOT EXISTS priority VARCHAR(50) DEFAULT NULL AFTER complexity;
