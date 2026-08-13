-- ============================================================
-- Clean Grid Table Content Data
-- ============================================================
-- Clears all transactional data from the grid-related tables
-- while preserving reference/lookup data (users, clients,
-- shows, departments, role_menu_permissions, etc.)
--
-- Use this to reset the project grid for fresh testing or
-- before re-importing data.
--
-- NOTE: Every DELETE is guarded by an existence check, so this script
-- keeps working even when a table is missing from the database (e.g.
-- older installs without chat/notifications tables). If a table is
-- absent, run full_setup.sql (or create the missing table) first, then
-- re-run this script.
-- ============================================================


-- Attachments linked to shots (content grid)

-- Chat messages linked to shots (content grid)

-- Notifications (transactional content)

-- Core shot/project grid data
-- Use DELETE instead of TRUNCATE because TRUNCATE is DDL and may
-- ignore FOREIGN_KEY_CHECKS = 0 in some MySQL configurations.
USE vfxpick_pipeline;

SET FOREIGN_KEY_CHECKS = 0;

-- Delete a table's contents only when the table actually exists
-- (prevents "#1932 - Table doesn't exist in engine" errors).
SET @tbl := 'attachments';
SET @sql := IF(
  EXISTS(SELECT 1 FROM information_schema.tables
         WHERE table_schema = DATABASE() AND table_name = @tbl),
  CONCAT('DELETE FROM `', @tbl, '`'),
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @tbl := 'chat_messages';
SET @sql := IF(
  EXISTS(SELECT 1 FROM information_schema.tables
         WHERE table_schema = DATABASE() AND table_name = @tbl),
  CONCAT('DELETE FROM `', @tbl, '`'),
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @tbl := 'notifications';
SET @sql := IF(
  EXISTS(SELECT 1 FROM information_schema.tables
         WHERE table_schema = DATABASE() AND table_name = @tbl),
  CONCAT('DELETE FROM `', @tbl, '`'),
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Core shot/grid data (the grid cannot exist without this table)
DELETE FROM shots;

SET FOREIGN_KEY_CHECKS = 1;

-- ============================================================
-- Verification (optional — uncomment to confirm)
-- ============================================================
-- SELECT 'attachments' AS tbl, COUNT(*) AS rows FROM attachments
-- UNION ALL
-- SELECT 'chat_messages',  COUNT(*) FROM chat_messages
-- UNION ALL
-- SELECT 'notifications',  COUNT(*) FROM notifications
-- UNION ALL
-- SELECT 'shots',          COUNT(*) FROM shots;
