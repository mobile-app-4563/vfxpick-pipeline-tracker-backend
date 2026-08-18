"""Database utilities with per-request connection reuse.

All functions now use `get_db_cursor()` which reuses a single DB connection for
the entire request lifetime.  This eliminates the overhead of opening/closing
pooled connections for every query.
"""

import json
from datetime import date, datetime
from functools import lru_cache

from database.connection import get_db_cursor


def run_query(query: str, params=None, fetch_one=False, fetch_all=False):
    """Execute a query.  Reuses the request-scoped connection when possible."""
    with get_db_cursor(commit=(not fetch_one and not fetch_all)) as cursor:
        cursor.execute(query, params or ())
        if fetch_one:
            return cursor.fetchone()
        if fetch_all:
            return cursor.fetchall()
        # No fetch flags (DML or discard-SELECT): consume any result set so the
        # connection has no unread results when commit() runs. C-extension
        # buffered cursors set `with_rows=False` for DML and fetchall() would
        # raise TypeError on the missing result buffer.
        try:
            if cursor.with_rows:
                cursor.fetchall()
        except Exception:
            pass
        return None


def run_query_many(queries: list) -> list:
    """Execute multiple SELECT queries on a single connection and return results.

    Each element of `queries` should be a (query_str, params_tuple) pair.
    Returns a list of result lists in the same order.
    """
    results = []
    with get_db_cursor() as cursor:
        for query, params in queries:
            cursor.execute(query, params)
            results.append(cursor.fetchall() or [])
    return results


def generate_prefixed_id(table_name: str, id_column: str, prefix: str, start_number: int):
    """Generate a prefixed ID by counting rows. Uses cached connection."""
    row = run_query(f"SELECT COUNT(*) AS cnt FROM {table_name}", fetch_one=True)
    return f"{prefix}{start_number + row['cnt'] + 1}"


# ── Cached lookup helpers ──────────────────────────────────────────────

@lru_cache(maxsize=64)
def _cached_user(user_id: str) -> dict | None:
    """Thread-safe LRU-cached user fetch.  Cache is thread-local per process."""
    with get_db_cursor() as cursor:
        cursor.execute(
            "SELECT user_id, name, email, department, role, level, status, avatar, phone, employee_id_ext FROM users WHERE user_id = %s",
            (user_id,),
        )
        return cursor.fetchone()


def get_user(user_id: str):
    """Return the full user row for the given user_id, or None.  Cached."""
    if not user_id:
        return None
    # Return a copy so callers can mutate without poisoning the cache
    row = _cached_user(user_id)
    return dict(row) if row else None


def invalidate_user_cache(user_id: str):
    """Clear the cached user entry (call after updates to user row)."""
    _cached_user.cache_clear()



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
