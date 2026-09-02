"""Database utilities with per-request connection reuse.

All functions now use `get_db_cursor()` which reuses a single DB connection for
the entire request lifetime.  This eliminates the overhead of opening/closing
pooled connections for every query.
"""

import json
import re
from datetime import date, datetime, timedelta
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
    """Generate a collision-safe prefixed ID.

    Count-based generation breaks when rows are deleted (COUNT drops but the
    highest ID stays), which hands out an already-used ID and trips the
    PRIMARY KEY.  We therefore start from the highest existing numeric suffix
    for the prefix (MAX-based) and walk upward until we find a free ID.
    """
    row = run_query(
        f"SELECT MAX(CAST(SUBSTRING({id_column}, %s) AS UNSIGNED)) AS max_num "
        f"FROM {table_name} WHERE {id_column} LIKE %s",
        (len(prefix) + 1, f"{prefix}%"),
        fetch_one=True,
    )
    candidate = max(row["max_num"] or 0, start_number) + 1
    while True:
        uid = f"{prefix}{candidate}"
        exists = run_query(
            f"SELECT 1 FROM {table_name} WHERE {id_column} = %s", (uid,), fetch_one=True
        )
        if not exists:
            return uid
        candidate += 1


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


# Excel's 2-digit-year window: 30→1930 … 99→1999, 00→2029.
# (Python's strptime %y would map 30→2030, so 2-digit years are resolved
# manually to stay consistent with the frontend's Excel semantics.)
def _excel_year_2(yy: int) -> int:
    return 1900 + yy if yy >= 30 else 2000 + yy


# Named month → 1–12 by first three letters ("March"/"Mar" → 3).
_MONTH_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Any 3–9 letter word (first three letters decide the month).
_MONTH_WORD = r"[A-Za-z]{3,9}"


def to_sql_date(value):
    """Validate and normalize a date value for DATE columns.

    Accepts ``datetime``/``date`` objects, ISO strings (``YYYY-MM-DD``,
    ``YYYY-MM-DD HH:MM:SS``), Excel serial numbers (numeric or numeric
    strings), and common text date formats — including the production
    template's ``mmm-yy`` ("May-25" → ``2025-05-01``) and ``mmm yyyy``
    ("March 2025" → ``2025-03-01``).  2-digit years use Excel's 1930–2029
    window ("Mar-30" → ``1930-03-01``), matching the frontend exactly.
    Returns the ``YYYY-MM-DD`` string.  Anything invalid — empty text,
    ``0000-00-00``, garbage — becomes ``None`` so MySQL never coerces bad
    input into a zero date again.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        value = str(value)
    text = value.strip()
    if not text:
        return None
    # Excel serial numbers occasionally arrive as numeric strings.  Guard
    # against year-first text like "2025" or "2025.05" (which float-parse
    # as small 1905-era serials) — those are dates, handled below.
    is_serial = False
    try:
        num = float(text)
        if 1 <= num < 70000:
            if re.fullmatch(r"\d{4}", text):
                year = int(text)
                if 1000 <= year <= 9999:
                    return f"{year:04d}-01-01"
            if not re.match(r"^\d{4}[-/.]", text):
                is_serial = True
    except ValueError:
        pass
    if is_serial:
        serial = int(float(text))
        return (datetime(1899, 12, 30) + timedelta(days=serial)).date().isoformat()

    # ── Named-month text dates ────────────────────────────────────────────
    # "May-25" / "March 2025" / "may 25" → the 1st of the month.
    m = re.fullmatch(rf"({_MONTH_WORD})[-/.\s](\d{{1,4}})", text, re.IGNORECASE)
    if m and m.group(1)[:3].lower() in _MONTH_NUM:
        mon = _MONTH_NUM[m.group(1)[:3].lower()]
        yy = m.group(2)
        year = _excel_year_2(int(yy)) if len(yy) < 3 else int(yy)
        return f"{year:04d}-{mon:02d}-01"
    # "25-May-2025" / "25 May 2025" / "25-May-25" (day + named month + year).
    m = re.fullmatch(
        rf"(\d{{1,2}})[-/.\s]({_MONTH_WORD})[-/.\s](\d{{2,4}})", text, re.IGNORECASE
    )
    if m and m.group(2)[:3].lower() in _MONTH_NUM:
        day = int(m.group(1))
        mon = _MONTH_NUM[m.group(2)[:3].lower()]
        yy = m.group(3)
        year = _excel_year_2(int(yy)) if len(yy) < 3 else int(yy)
        return f"{year:04d}-{mon:02d}-{day:02d}"
    # "May 25, 2025" / "May 25 2025" (named month + day + year).
    m = re.fullmatch(
        rf"({_MONTH_WORD})[-/.\s](\d{{1,2}})[,.\s]+(\d{{2,4}})", text, re.IGNORECASE
    )
    if m and m.group(1)[:3].lower() in _MONTH_NUM:
        mon = _MONTH_NUM[m.group(1)[:3].lower()]
        day = int(m.group(2))
        yy = m.group(3)
        year = _excel_year_2(int(yy)) if len(yy) < 3 else int(yy)
        return f"{year:04d}-{mon:02d}-{day:02d}"
    # "5-May" / "01-May" (day + named month, no year) → current-year day.
    m = re.fullmatch(rf"(\d{{1,2}})[-/.\s]({_MONTH_WORD})", text, re.IGNORECASE)
    if m and m.group(2)[:3].lower() in _MONTH_NUM:
        day = int(m.group(1))
        mon = _MONTH_NUM[m.group(2)[:3].lower()]
        return f"{datetime.now().year:04d}-{mon:02d}-{day:02d}"

    # ── Numeric text dates ────────────────────────────────────────────────
    # Day-first with 2-digit year: "01-05-25" / "25/5/25" (Excel window).
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2})", text)
    if m:
        a, b, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12 >= b:
            day, mon = a, b
        elif b > 12 >= a:
            day, mon = b, a
        else:
            day, mon = a, b  # ambiguous → day-first, same as the frontend
        return f"{_excel_year_2(yy):04d}-{mon:02d}-{day:02d}"
    # Month-year with 2-digit year: "05-25" → the 1st of the month.
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{2})", text)
    if m:
        mon, yy = int(m.group(1)), int(m.group(2))
        if 1 <= mon <= 12:
            return f"{_excel_year_2(yy):04d}-{mon:02d}-01"

    # ── strptime fallback for unambiguous 4-digit-year formats ───────────
    # ISO / standard datetime strings ("2025-05-01", "2025-05-01 10:30:00").
    for fmt in (
        "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
        "%Y/%m/%d", "%Y.%m.%d",
    ):
        try:
            return datetime.strptime(text[:19], fmt).date().isoformat()
        except ValueError:
            continue
    # Year-month only: "2025-05" / "2025/05" / "2025.05" → 1st of month.
    for fmt in ("%Y-%m", "%Y/%m", "%Y.%m"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    # Numeric day-first / month-year with 4-digit years.
    for fmt in (
        "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %m %Y",
        "%m-%Y", "%m/%Y", "%m.%Y",
    ):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


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
