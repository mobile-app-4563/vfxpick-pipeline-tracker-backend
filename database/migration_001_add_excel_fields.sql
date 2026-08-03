-- Migration: Add all department requirement / Excel header fields to shots table
-- NOTE: If you are running full_setup.sql fresh (v2+), these columns are already
-- included in the CREATE TABLE shots statement and this migration is NOT needed.
-- Run this ALTER TABLE only if your shots table was created from the original schema.

ALTER TABLE shots
    ADD COLUMN coordinator        VARCHAR(100)   DEFAULT NULL AFTER artist_status,
    ADD COLUMN total_frames       INT            DEFAULT 0  AFTER frame_out,
    ADD COLUMN level_of_shot      VARCHAR(50)    DEFAULT NULL AFTER artist_id,
    ADD COLUMN allocation_date    DATE           DEFAULT NULL AFTER level_of_shot,
    ADD COLUMN allocation_eta     DATE           DEFAULT NULL AFTER allocation_date,
    ADD COLUMN starting_date      DATE           DEFAULT NULL AFTER allocation_eta,
    ADD COLUMN complete_date      DATE           DEFAULT NULL AFTER starting_date,
    ADD COLUMN daily_wip          DECIMAL(5,2)   DEFAULT 0  AFTER complete_date,
    ADD COLUMN consumed_mandays   DECIMAL(6,2)   DEFAULT 0  AFTER mandays,
    ADD COLUMN saved_mandays      DECIMAL(6,2)   DEFAULT 0  AFTER consumed_mandays,
    ADD COLUMN approved_version   VARCHAR(50)    DEFAULT NULL AFTER saved_mandays,
    ADD COLUMN approved_by        VARCHAR(100)   DEFAULT NULL AFTER approved_version,
    ADD COLUMN comments           TEXT           DEFAULT NULL AFTER approved_by,
    ADD COLUMN complexity         VARCHAR(50)    DEFAULT NULL AFTER comments,
    ADD COLUMN from_roto          TEXT           DEFAULT NULL AFTER client_feedback,
    ADD COLUMN from_paint         TEXT           DEFAULT NULL AFTER from_roto,
    ADD COLUMN from_mm            TEXT           DEFAULT NULL AFTER from_paint,
    ADD COLUMN from_comp          TEXT           DEFAULT NULL AFTER from_mm;
