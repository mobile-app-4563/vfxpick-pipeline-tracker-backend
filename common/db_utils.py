import json
from datetime import date, datetime

from database.connection import get_db


def run_query(query: str, params=None, fetch_one=False, fetch_all=False):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(query, params or ())
        if fetch_one:
            return cursor.fetchone()
        if fetch_all:
            return cursor.fetchall()
        conn.commit()
        return None
    finally:
        cursor.close()
        conn.close()


def generate_prefixed_id(table_name: str, id_column: str, prefix: str, start_number: int):
    row = run_query(f"SELECT COUNT(*) AS cnt FROM {table_name}", fetch_one=True)
    return f"{prefix}{start_number + row['cnt'] + 1}"


def get_user(user_id: str):
    """Return the full user row for the given user_id, or None."""
    if not user_id:
        return None
    return run_query(
        "SELECT user_id, name, email, department, role, level, status, avatar FROM users WHERE user_id = %s",
        (user_id,),
        fetch_one=True,
    )


def initials(name: str) -> str:
    parts = [part for part in name.strip().split() if part]
    if not parts:
        return ""
    first = parts[0][0]
    second = parts[1][0] if len(parts) > 1 else ""
    return (first + second).upper()


def to_iso(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def parse_assets(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def create_notification(message: str, notif_type: str = "System Notification", user_id=None):
    notif_id = generate_prefixed_id("notifications", "id", "NTF", 0)
    run_query(
        """
        INSERT INTO notifications (id, user_id, message, type, is_read)
        VALUES (%s, %s, %s, %s, FALSE)
        """,
        (notif_id, user_id, message, notif_type),
    )
