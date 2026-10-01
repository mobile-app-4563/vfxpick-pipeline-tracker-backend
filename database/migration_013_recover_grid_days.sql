-- ============================================================================
-- Migration 013 — recover the DAY of production_grid dates stored as mmm-yy
-- ============================================================================
-- The Jan-Dec Excel template stores its dates as `mmm-yy` cells (numFmtId 17),
-- which Excel renders as "Aug-25" while the cell literally holds 1 Aug 2025.
-- The data means the 25TH of August, so an older import wrote the 1st of the
-- month into the table and the two-digit DAY was left behind in the year.
--
-- Result: every stored date had DAY(col) = 1 and a meaningless year
-- (samples seen: 1930, 1931, 2004, 2018, 2021, 2024, 2028, 2029), so the grid
-- showed month-YEAR labels and the Home "Due Today / Due Tomorrow" pickouts
-- (`DATE_FORMAT(eta, '%m-%d')`) could only ever match on the 1st of a month.
--
-- The day is still recoverable from the year's last two digits — the exact rule
-- the import now applies (`excelEtaToIso` / `repairStoredMonthDayIso`):
--     2025-08-01 → 25 Aug      2018-09-01 → 18 Sep      1930-03-01 → 30 Mar
-- so the repaired rows are IDENTICAL to what a re-import produces today.
--
-- A day is only recovered when it can really exist:
--   * the value is still on the 1st of a month (never touch a real day),
--   * the two-digit year is 02–31 (00/01 carry no day to recover),
--   * the day exists in that month, validated against the leap year 2000
--     (31 April and 30 February keep the 1st), and
--   * 29 February exists in the current year.
-- Anything else is left exactly as it was.
--
-- Idempotent: a repaired row no longer has DAY(col) = 1, so re-running is a
-- no-op. Step 1 keeps a reversible copy of the original values.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Backup the date columns before touching anything (reversible).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS production_grid_date_backup_013 AS
SELECT
    grid_id,
    shots_received_date,
    wip_eta,
    eta,
    delivered_on,
    fl_eta,
    NOW() AS backed_up_at
FROM production_grid;

-- ---------------------------------------------------------------------------
-- 2. Recover the day for all five date columns.
-- ---------------------------------------------------------------------------
UPDATE production_grid
SET
    shots_received_date = CASE
        WHEN shots_received_date IS NULL OR DAY(shots_received_date) <> 1
            THEN shots_received_date
        WHEN YEAR(shots_received_date) % 100 BETWEEN 2 AND 31
             AND YEAR(shots_received_date) % 100
                 <= DAY(LAST_DAY(DATE_FORMAT(shots_received_date, '2000-%m-01')))
             AND NOT (MONTH(shots_received_date) = 2
                      AND YEAR(shots_received_date) % 100 = 29
                      AND DAY(LAST_DAY(CONCAT(YEAR(CURDATE()), '-02-01'))) < 29)
            THEN STR_TO_DATE(
                     CONCAT(YEAR(CURDATE()), '-',
                            LPAD(MONTH(shots_received_date), 2, '0'), '-',
                            LPAD(YEAR(shots_received_date) % 100, 2, '0')),
                     '%Y-%m-%d')
        ELSE shots_received_date
    END,
    wip_eta = CASE
        WHEN wip_eta IS NULL OR DAY(wip_eta) <> 1
            THEN wip_eta
        WHEN YEAR(wip_eta) % 100 BETWEEN 2 AND 31
             AND YEAR(wip_eta) % 100
                 <= DAY(LAST_DAY(DATE_FORMAT(wip_eta, '2000-%m-01')))
             AND NOT (MONTH(wip_eta) = 2
                      AND YEAR(wip_eta) % 100 = 29
                      AND DAY(LAST_DAY(CONCAT(YEAR(CURDATE()), '-02-01'))) < 29)
            THEN STR_TO_DATE(
                     CONCAT(YEAR(CURDATE()), '-',
                            LPAD(MONTH(wip_eta), 2, '0'), '-',
                            LPAD(YEAR(wip_eta) % 100, 2, '0')),
                     '%Y-%m-%d')
        ELSE wip_eta
    END,
    eta = CASE
        WHEN eta IS NULL OR DAY(eta) <> 1
            THEN eta
        WHEN YEAR(eta) % 100 BETWEEN 2 AND 31
             AND YEAR(eta) % 100
                 <= DAY(LAST_DAY(DATE_FORMAT(eta, '2000-%m-01')))
             AND NOT (MONTH(eta) = 2
                      AND YEAR(eta) % 100 = 29
                      AND DAY(LAST_DAY(CONCAT(YEAR(CURDATE()), '-02-01'))) < 29)
            THEN STR_TO_DATE(
                     CONCAT(YEAR(CURDATE()), '-',
                            LPAD(MONTH(eta), 2, '0'), '-',
                            LPAD(YEAR(eta) % 100, 2, '0')),
                     '%Y-%m-%d')
        ELSE eta
    END,
    delivered_on = CASE
        WHEN delivered_on IS NULL OR DAY(delivered_on) <> 1
            THEN delivered_on
        WHEN YEAR(delivered_on) % 100 BETWEEN 2 AND 31
             AND YEAR(delivered_on) % 100
                 <= DAY(LAST_DAY(DATE_FORMAT(delivered_on, '2000-%m-01')))
             AND NOT (MONTH(delivered_on) = 2
                      AND YEAR(delivered_on) % 100 = 29
                      AND DAY(LAST_DAY(CONCAT(YEAR(CURDATE()), '-02-01'))) < 29)
            THEN STR_TO_DATE(
                     CONCAT(YEAR(CURDATE()), '-',
                            LPAD(MONTH(delivered_on), 2, '0'), '-',
                            LPAD(YEAR(delivered_on) % 100, 2, '0')),
                     '%Y-%m-%d')
        ELSE delivered_on
    END,
    fl_eta = CASE
        WHEN fl_eta IS NULL OR DAY(fl_eta) <> 1
            THEN fl_eta
        WHEN YEAR(fl_eta) % 100 BETWEEN 2 AND 31
             AND YEAR(fl_eta) % 100
                 <= DAY(LAST_DAY(DATE_FORMAT(fl_eta, '2000-%m-01')))
             AND NOT (MONTH(fl_eta) = 2
                      AND YEAR(fl_eta) % 100 = 29
                      AND DAY(LAST_DAY(CONCAT(YEAR(CURDATE()), '-02-01'))) < 29)
            THEN STR_TO_DATE(
                     CONCAT(YEAR(CURDATE()), '-',
                            LPAD(MONTH(fl_eta), 2, '0'), '-',
                            LPAD(YEAR(fl_eta) % 100, 2, '0')),
                     '%Y-%m-%d')
        ELSE fl_eta
    END
WHERE
    DAY(shots_received_date) = 1
    OR DAY(wip_eta) = 1
    OR DAY(eta) = 1
    OR DAY(delivered_on) = 1
    OR DAY(fl_eta) = 1;

-- ---------------------------------------------------------------------------
-- 3. Verify: no recoverable day-1 value should be left behind.
-- ---------------------------------------------------------------------------
SELECT
    COUNT(*)                                                    AS total_rows,
    SUM(DAY(shots_received_date) = 1)                           AS shots_day1,
    SUM(DAY(wip_eta) = 1)                                       AS wip_eta_day1,
    SUM(DAY(eta) = 1)                                           AS eta_day1,
    SUM(DAY(delivered_on) = 1)                                  AS delivered_day1,
    SUM(DAY(fl_eta) = 1)                                        AS fl_eta_day1
FROM production_grid;

-- Rollback (if ever needed):
--   UPDATE production_grid g
--   JOIN production_grid_date_backup_013 b ON b.grid_id = g.grid_id
--   SET g.shots_received_date = b.shots_received_date,
--       g.wip_eta             = b.wip_eta,
--       g.eta                 = b.eta,
--       g.delivered_on        = b.delivered_on,
--       g.fl_eta              = b.fl_eta;
