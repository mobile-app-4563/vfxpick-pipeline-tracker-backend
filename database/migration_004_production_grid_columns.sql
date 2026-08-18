-- ============================================================
-- Migration 004: Add Production Management Grid columns to shots
-- Adds the extra Excel-template columns that are not present in
-- the shots table, so the Production Management grid can show
-- ALL 20 columns of the "excel Jan - dec format" template.
-- ============================================================

USE vfxpick_pipeline;

-- ------------------------------------------------------------
-- ALTER TABLE shots
-- New columns mapped from the Excel template:
--   3.  Month              -> month
--   5.  Client for Ref     -> client_for_ref
--   8.  WIP ETA            -> wip_eta
--   16. Work station       -> work_station
--   18. Approved Client MD -> approved_client_md
--   19. FL ETA             -> fl_eta
--   20. FL Man-days        -> fl_mandays
-- ------------------------------------------------------------
ALTER TABLE shots
    ADD COLUMN month            VARCHAR(20)  DEFAULT NULL AFTER coordinator,
    ADD COLUMN client_for_ref   VARCHAR(100) DEFAULT NULL AFTER allocated_date,
    ADD COLUMN wip_eta          DATE         DEFAULT NULL AFTER client_for_ref,
    ADD COLUMN work_station     VARCHAR(100) DEFAULT NULL AFTER complete_date,
    ADD COLUMN approved_client_md DECIMAL(6,2) DEFAULT 0 AFTER mandays,
    ADD COLUMN fl_eta           DATE         DEFAULT NULL AFTER approved_client_md,
    ADD COLUMN fl_mandays       DECIMAL(6,2) DEFAULT 0 AFTER fl_eta;
