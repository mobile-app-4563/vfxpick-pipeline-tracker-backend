"""Shared JSON serializers (camelCase keys to match the Flutter models)."""

from common.db_utils import to_iso


def shot_to_json(row: dict) -> dict:
    """Serialize a joined shots row into a camelCase payload.

    The row is expected to contain shot columns plus (optionally) joined
    show/client/artist fields aliased as show_name, client_id, client_name,
    artist_name.
    """
    return {
        "shotId": row.get("shot_id"),
        "showId": row.get("show_id"),
        "showName": row.get("show_name"),
        "clientId": row.get("client_id"),
        "clientName": row.get("client_name"),
        "department": row.get("department"),
        "shotCode": row.get("shot_code"),
        "frameIn": row.get("frame_in"),
        "frameOut": row.get("frame_out"),
        "supervisorBid": float(row["supervisor_bid"]) if row.get("supervisor_bid") is not None else 0.0,
        "clientBid": float(row["client_bid"]) if row.get("client_bid") is not None else 0.0,
        "clientEta": to_iso(row.get("client_eta")),
        "notes": row.get("notes"),
        "status": row.get("status"),
        "artistId": row.get("artist_id"),
        "artistName": row.get("artist_name"),
        "artistBid": float(row["artist_bid"]) if row.get("artist_bid") is not None else 0.0,
        "artistEta": to_iso(row.get("artist_eta")),
        "description": row.get("description"),
        "supervisorStatus": row.get("supervisor_status"),
        "artistStatus": row.get("artist_status"),
        "allocatedDate": to_iso(row.get("allocated_date")),
        "mandays": float(row["mandays"]) if row.get("mandays") is not None else 0.0,
        "dueDate": to_iso(row.get("due_date")),
        "clientFeedback": row.get("client_feedback"),
        "createdAt": to_iso(row.get("created_at")),
        "updatedAt": to_iso(row.get("updated_at")),
    }


# Columns + joins reused by most shot queries.
SHOT_SELECT = """
    SELECT s.*, sh.show_name, sh.client_id, c.client_name, u.name AS artist_name
    FROM shots s
    JOIN shows sh ON s.show_id = sh.show_id
    JOIN clients c ON sh.client_id = c.client_id
    LEFT JOIN users u ON s.artist_id = u.user_id
"""
