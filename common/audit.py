"""Shared activity audit logging for user-visible module changes."""

import json

from common.db_utils import run_query


def ensure_activity_table():
    run_query(
        """
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
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def write_activity_log(
    changed_by_user_id,
    module,
    action,
    entity_type,
    entity_id=None,
    details=None,
):
    try:
        ensure_activity_table()
        run_query(
            """
            INSERT INTO activity_audit_log
                (changed_by_user_id, module, action, entity_type, entity_id, details)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                changed_by_user_id,
                module,
                action,
                entity_type,
                entity_id,
                json.dumps(details or {}, default=str),
            ),
        )
    except Exception:
        # Auditing must never break the user's primary operation.
        pass


def fetch_activity_logs(limit=500):
    ensure_activity_table()
    rows = run_query(
        """
        SELECT a.id, a.changed_by_user_id, u.name AS changed_by_name,
               u.email AS changed_by_username, a.module, a.action,
               a.entity_type, a.entity_id, a.details, a.changed_at
        FROM activity_audit_log a
        LEFT JOIN users u ON u.user_id = a.changed_by_user_id
        ORDER BY a.changed_at DESC, a.id DESC
        LIMIT %s
        """,
        (limit,),
        fetch_all=True,
    ) or []
    result = []
    for row in rows:
        details = row.get("details")
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except (TypeError, ValueError):
                details = {"message": details}
        result.append(
            {
                "id": int(row["id"]),
                "changedByUserId": row["changed_by_user_id"],
                "changedByName": row.get("changed_by_name"),
                "changedByUsername": row.get("changed_by_username"),
                "module": row["module"],
                "action": row["action"],
                "entityType": row["entity_type"],
                "entityId": row.get("entity_id"),
                "details": details or {},
                "changedAt": row["changed_at"].isoformat() if row.get("changed_at") else None,
                "route": row["module"],
            }
        )
    return result
