-- ============================================================
-- Clean Grid Table Content Data
-- ============================================================
-- Clears all transactional data from the grid-related tables
-- while preserving reference/lookup data (users, clients,
-- shows, departments, role_menu_permissions, etc.)
--
-- Use this to reset the project grid for fresh testing or
-- before re-importing data.
-- ============================================================


-- Attachments linked to shots (content grid)

-- Chat messages linked to shots (content grid)

-- Notifications (transactional content)

-- Core shot/project grid data
-- Use DELETE instead of TRUNCATE because TRUNCATE is DDL and may
-- ignore FOREIGN_KEY_CHECKS = 0 in some MySQL configurations.
USE vfxpick_pipeline;

SET FOREIGN_KEY_CHECKS = 0;
DELETE FROM attachments;
DELETE FROM chat_messages;
DELETE FROM notifications;
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
