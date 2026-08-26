"""Assets routes.

Supporting materials / shared documents attached to shots. Adding an
attachment raises a notification (feature iv: shared document / file attachment).
"""

from flask import Blueprint, request

from auth.middleware import token_required
from common.audit import write_activity_log
from common.db_utils import create_notification, generate_prefixed_id, get_user, run_query, to_iso
from common.http import failure, success

assets_bp = Blueprint("assets", __name__)


def _serialize(row):
    return {
        "attachmentId": row["attachment_id"],
        "shotId": row["shot_id"],
        "uploadedBy": row["uploaded_by"],
        "uploaderName": row.get("uploader_name"),
        "fileName": row["file_name"],
        "fileUrl": row["file_url"],
        "fileType": row["file_type"],
        "createdAt": to_iso(row["created_at"]),
    }


@assets_bp.route("", methods=["GET"])
@assets_bp.route("/", methods=["GET"])
@token_required
def list_assets(_current_user_id):
    shot_id = request.args.get("shotId")
    query = """
        SELECT a.*, u.name AS uploader_name
        FROM attachments a
        LEFT JOIN users u ON a.uploaded_by = u.user_id
    """
    params = []
    if shot_id:
        query += " WHERE a.shot_id = %s"
        params.append(shot_id)
    query += " ORDER BY a.created_at DESC"
    rows = run_query(query, tuple(params), fetch_all=True) or []
    return success({"assets": [_serialize(r) for r in rows]})


@assets_bp.route("", methods=["POST"])
@assets_bp.route("/", methods=["POST"])
@token_required
def add_asset(current_user_id):
    data = request.get_json(silent=True) or {}
    file_name = (data.get("fileName") or "").strip()
    file_url = (data.get("fileUrl") or "").strip()
    shot_id = data.get("shotId")

    if not file_name or not file_url:
        return failure("fileName and fileUrl are required.", 400)

    attachment_id = generate_prefixed_id("attachments", "attachment_id", "ATT", 0)
    run_query(
        """
        INSERT INTO attachments (attachment_id, shot_id, uploaded_by, file_name, file_url, file_type)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (attachment_id, shot_id, current_user_id, file_name, file_url, data.get("fileType")),
    )
    write_activity_log(
        current_user_id,
        "Assets",
        "CREATE",
        "Attachment",
        attachment_id,
        {"fileName": file_name, "shotId": shot_id, "fileUrl": file_url},
    )

    if shot_id:
        shot = run_query(
            "SELECT department, shot_code FROM shots WHERE shot_id = %s", (shot_id,), fetch_one=True
        )
        uploader = get_user(current_user_id)
        if shot:
            dept_parts = [d.strip() for d in (shot["department"] or "").split(",") if d.strip()]
            if dept_parts:
                supervisors = run_query(
                    f"""
                    SELECT user_id FROM users
                    WHERE department IN ({', '.join(['%s'] * len(dept_parts))}) AND role IN ('Supervisor', 'Team Lead') AND status = 'Active'
                    """,
                    tuple(dept_parts),
                    fetch_all=True,
                ) or []
                sender = uploader["name"] if uploader else "Someone"
                for sup in supervisors:
                    if sup["user_id"] != current_user_id:
                        create_notification(
                            f"{sender} shared a document on shot {shot['shot_code']}.",
                            "Attachment Shared",
                            sup["user_id"],
                        )

    row = run_query(
        """
        SELECT a.*, u.name AS uploader_name
        FROM attachments a LEFT JOIN users u ON a.uploaded_by = u.user_id
        WHERE a.attachment_id = %s
        """,
        (attachment_id,),
        fetch_one=True,
    )
    return success({"asset": _serialize(row)}, 201)


@assets_bp.route("/<attachment_id>", methods=["DELETE"])
@token_required
def delete_asset(_current_user_id, attachment_id):
    existing = run_query(
        "SELECT attachment_id FROM attachments WHERE attachment_id = %s",
        (attachment_id,),
        fetch_one=True,
    )
    if not existing:
        return failure("Attachment not found", 404)
    run_query("DELETE FROM attachments WHERE attachment_id = %s", (attachment_id,))
    write_activity_log(
        _current_user_id,
        "Assets",
        "DELETE",
        "Attachment",
        attachment_id,
        {},
    )
    return success({"message": "Attachment deleted", "attachmentId": attachment_id})
