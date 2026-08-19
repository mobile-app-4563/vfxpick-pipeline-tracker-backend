-- Migration 007: expand production_grid.status ENUM for the new production statuses
-- Run manually against existing installs:  mysql -u root vfxpick_pipeline < migration_007_production_grid_status_enum.sql
--
-- The status column previously allowed only the 4 shot statuses. The Production
-- grid now tracks bidding/WIP/delivery states, so the ENUM is widened. Use
-- ALTER TABLE ... MODIFY with the full list (ENUM values cannot be conditionally
-- added; this statement is idempotent because it re-declares the same list).

ALTER TABLE production_grid
    MODIFY COLUMN status ENUM(
        'Hold',
        'Approved',
        'Awaiting Approval',
        'Approved Internal',
        'Bidding',
        'Bids Received',
        'WIP',
        'Delivered',
        'Awaiting Reference',
        'Awaiting Plates',
        'Completed',
        'RTU',
        'Rough Cost Shared'
    ) NOT NULL DEFAULT 'Awaiting Approval';
