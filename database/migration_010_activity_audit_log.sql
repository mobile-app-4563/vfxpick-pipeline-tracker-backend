-- Shared activity history for the Audit Logs menu.
CREATE TABLE IF NOT EXISTS activity_audit_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    changed_by_user_id VARCHAR(20) NOT NULL,
    module VARCHAR(80) NOT NULL,
    action VARCHAR(40) NOT NULL,
    entity_type VARCHAR(80) NOT NULL,
    entity_id VARCHAR(100) DEFAULT NULL,
    details JSON DEFAULT NULL,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_activity_changed_at (changed_at DESC),
    INDEX idx_activity_actor (changed_by_user_id),
    INDEX idx_activity_module (module)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;