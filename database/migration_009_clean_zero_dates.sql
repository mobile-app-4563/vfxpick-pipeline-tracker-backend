-- ============================================================================
-- Migration 009 — NULL out MySQL zero-dates (0000-00-00)
-- ----------------------------------------------------------------------------
-- The Excel template's empty date cells (WIP ETA, Delivered on, FL ETA) were
-- coerced by MySQL into '0000-00-00'. The frontend now parses dates strictly
-- and the backend validates them (common.db_utils.to_sql_date), so this is a
-- one-time cleanup of legacy rows. Real dates — including the template's
-- 'mmm-yy' month values such as 1930-03-01 / 2025-05-01 — are preserved.
-- ============================================================================

-- production_grid (empty template cells → NULL)
UPDATE production_grid SET wip_eta       = NULL WHERE wip_eta       < '1900-01-01';
UPDATE production_grid SET delivered_on  = NULL WHERE delivered_on  < '1900-01-01';
UPDATE production_grid SET fl_eta        = NULL WHERE fl_eta        < '1900-01-01';

-- shots (coerced garbage → NULL)
UPDATE shots SET starting_date = NULL WHERE starting_date < '1900-01-01';
