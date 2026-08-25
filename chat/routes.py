"""Chat routes.

A per-shot chat interface with attachment support (features ii & iii).
New messages raise notifications to the other shot participants (feature iv).
"""

from flask import Blueprint, request

from auth.middleware import token_required
from common.db_utils import create_notification, generate_prefixed_id, get_user, run_query, to_iso
from common.http import failure, success

chat_bp = Blueprint("chat", __name__)


def _serialize(row):
    return {
        "messageId": row["message_id"],
        "shotId": row["shot_id"],
        "senderId": row["sender_id"],
        "senderName": row.get("sender_name"),
        "senderAvatar": row.get("sender_avatar"),
        "message": row["message"],
        "attachmentName": row["attachment_name"],
        "attachmentUrl": row["attachment_url"],
        "createdAt": to_iso(row["created_at"]),
    }


@chat_bp.route("", methods=["GET"])
@chat_bp.route("/", methods=["GET"])
@token_required
def list_messages(_current_user_id):
    shot_id = request.args.get("shotId")
    if not shot_id:
        return failure("shotId is required.", 400)
    rows = run_query(
        """
        SELECT m.*, u.name AS sender_name, u.avatar AS sender_avatar
        FROM chat_messages m
        LEFT JOIN users u ON m.sender_id = u.user_id
        WHERE m.shot_id = %s
        ORDER BY m.created_at ASC
        """,
        (shot_id,),
        fetch_all=True,
    ) or []
    return success({"messages": [_serialize(r) for r in rows]})


@chat_bp.route("", methods=["POST"])
@chat_bp.route("/", methods=["POST"])
@token_required
def send_message(current_user_id):
    data = request.get_json(silent=True) or {}
    shot_id = data.get("shotId")
    message = (data.get("message") or "").strip()
    attachment_name = data.get("attachmentName")
    attachment_url = data.get("attachmentUrl")

    if not shot_id:
        return failure("shotId is required.", 400)
    if not message and not attachment_url:
        return failure("A message or attachment is required.", 400)

    shot = run_query(
        "SELECT shot_code, department, artist_id FROM shots WHERE shot_id = %s",
        (shot_id,),
        fetch_one=True,
    )
    if not shot:
        return failure("Shot not found", 404)

    message_id = generate_prefixed_id("chat_messages", "message_id", "MSG", 0)
    run_query(
        """
        INSERT INTO chat_messages (message_id, shot_id, sender_id, message, attachment_name, attachment_url)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (message_id, shot_id, current_user_id, message or "", attachment_name, attachment_url),
    )

    # Notify other participants (assigned artist + department supervisors).
    sender = get_user(current_user_id)
    sender_name = sender["name"] if sender else "Someone"
    recipients = set()
    if shot["artist_id"]:
        recipients.add(shot["artist_id"])
    dept_parts = [d.strip() for d in (shot.get("department") or "").split(",") if d.strip()]
    if dept_parts:
        supervisors = run_query(
            f"""
            SELECT user_id FROM users
            WHERE department IN ({', '.join(['%s'] * len(dept_parts))}) AND role IN ('Supervisor', 'Team Lead') AND status = 'Active'
            """,
            tuple(dept_parts),
            fetch_all=True,
        ) or []
        recipients.update(s["user_id"] for s in supervisors)
    recipients.discard(current_user_id)

    notif_type = "Attachment Shared" if attachment_url else "New Message"
    for recipient in recipients:
        create_notification(
            f"{sender_name} posted on shot {shot['shot_code']}.",
            notif_type,
            recipient,
        )

    row = run_query(
        """
        SELECT m.*, u.name AS sender_name, u.avatar AS sender_avatar
        FROM chat_messages m LEFT JOIN users u ON m.sender_id = u.user_id
        WHERE m.message_id = %s
        """,
        (message_id,),
        fetch_one=True,
    )
    return success({"message": _serialize(row)}, 201)
