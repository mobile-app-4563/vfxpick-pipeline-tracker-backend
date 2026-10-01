-- ============================================================
-- migration_012: production_grid.source_row_ref
-- ------------------------------------------------------------
-- WHY THIS EXISTS
-- The Excel/CSV import used to identify a grid row by
--     (client_name, show_name, tasks, shot_code, review_notes)
-- That key is NOT unique in real spreadsheets. The same shot and department
-- legitimately appears on many separate lines - one per element / submission /
-- artist - and each line carries its own Frames, ETA, Shot man-days and
-- Status. The old import silently collapsed all of those lines into one row.
--
-- Measured on the real sample file (jan_dec_full.xlsx, single sheet):
--     physical lines ................ 11,016
--     lines the old import kept ..... 9,379   <-- 1,637 lost
--     duplicate groups it created ... 761
--     of those, truly identical ..... 1
--     of those, DIFFERENT real data . 760
-- e.g. shot 32326 / ROTO / "Final locked QT received" = 87 separate lines that
-- are all distinct in the Frames column.
--
-- WHAT THIS COLUMN DOES
-- source_row_ref stores the line's row number inside the imported file, e.g.
-- "Sheet1!12" for Excel or "csv:12" / "paste:12" for text. It becomes part of
-- the import's row identity, so:
--   * every physical spreadsheet line keeps its own grid row, and
--   * re-importing the SAME file updates those same rows instead of
--     duplicating them (the import stays idempotent).
-- Rows created by hand keep source_row_ref = NULL and behave exactly as before.
--
-- SAFETY
-- Running this against an existing database is safe - the column is checked
-- first. The backend also adds it on demand at import time (see
-- _ensure_grid_source_row_ref in production/routes.py), so running this file
-- by hand is optional.
-- ============================================================

SET @col_exists := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'production_grid'
      AND COLUMN_NAME  = 'source_row_ref'
);

SET @ddl := IF(
    @col_exists = 0,
    'ALTER TABLE production_grid
        ADD COLUMN source_row_ref VARCHAR(80) DEFAULT NULL AFTER review_notes,
        ADD INDEX idx_production_grid_source_ref (source_row_ref)',
    'SELECT ''production_grid.source_row_ref already exists'' AS note'
);

PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
