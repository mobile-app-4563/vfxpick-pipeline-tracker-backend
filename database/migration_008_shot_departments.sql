-- Migration 008: widen shots.department to hold comma-separated department lists
-- A shot can belong to multiple departments (e.g. 'ROTO,PAINT'), so the column
-- must fit more than 20 characters.
-- Run manually against existing installs:  mysql -u root vfxpick_pipeline < migration_008_shot_departments.sql
-- NOTE: ALTER ... MODIFY is idempotent (re-running just re-applies the same DDL).

ALTER TABLE shots
    MODIFY department VARCHAR(255) NOT NULL;
