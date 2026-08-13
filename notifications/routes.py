"""Notifications routes — per-user notification center."""

from flask import Blueprint

from auth.middleware import token_required
from common.db_utils import run_query, to_iso
from common.http import failure, success

notifications_bp = Blueprint("notifications", __name__)


def _serialize(row):
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "message": row["message"],
        "type": row["type"],
        "isRead": bool(row["is_read"]),
        "timestamp": to_iso(row["timestamp"]),
    }


@notifications_bp.route("", methods=["GET"])
@notifications_bp.route("/", methods=["GET"])
@token_required
def list_notifications(current_user_id):
    rows = run_query(
        """
        SELECT * FROM notifications
        WHERE user_id = %s OR user_id IS NULL
        ORDER BY timestamp DESC
        """,
        (current_user_id,),
        fetch_all=True,
    ) or []
    return success({"notifications": [_serialize(r) for r in rows]})


@notifications_bp.route("/unread-count", methods=["GET"])
@token_required
def unread_count(current_user_id):
    row = run_query(
        """
        SELECT COUNT(*) AS cnt FROM notifications
        WHERE (user_id = %s OR user_id IS NULL) AND is_read = FALSE
        """,
        (current_user_id,),
        fetch_one=True,
    )
    return success({"count": int(row["cnt"]) if row else 0})


@notifications_bp.route("/<notification_id>/read", methods=["PATCH"])
@token_required
def mark_read(current_user_id, notification_id):
    existing = run_query(
        "SELECT id FROM notifications WHERE id = %s", (notification_id,), fetch_one=True
    )
    if not existing:
        return failure("Notification not found", 404)
    run_query("UPDATE notifications SET is_read = TRUE WHERE id = %s", (notification_id,))
    return success({"message": "Marked as read", "id": notification_id})


@notifications_bp.route("/mark-all-read", methods=["POST"])
@token_required
def mark_all_read(current_user_id):
    run_query(
        "UPDATE notifications SET is_read = TRUE WHERE user_id = %s OR user_id IS NULL",
        (current_user_id,),
    )
    return success({"message": "All notifications marked as read"})


@notifications_bp.route("/clear-all", methods=["DELETE"])
@token_required
def clear_all(current_user_id):
    run_query("DELETE FROM notifications WHERE user_id = %s", (current_user_id,))
    return success({"message": "Notifications cleared"})
