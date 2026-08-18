import os
from contextlib import contextmanager
from threading import Lock, local

from mysql.connector import InterfaceError
from mysql.connector import ProgrammingError
from mysql.connector import DatabaseError
from mysql.connector import pooling
from dotenv import load_dotenv


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
load_dotenv(os.path.join(ROOT_DIR, ".env"))


_db_pool = None
_pool_lock = Lock()
# Thread-local storage for per-request connection reuse
_tls = local()


def _get_required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Set DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, and DB_NAME in .env."
        )
    return value.strip()


def _build_pool():
    return pooling.MySQLConnectionPool(
        pool_name="vfxpick_pool",
        pool_size=int(os.getenv("DB_POOL_SIZE", "20")),
        pool_reset_session=True,
        host=os.getenv("DB_HOST", "localhost").strip(),
        port=int(os.getenv("DB_PORT", 3306)),
        user=_get_required_env("DB_USER"),
        password=_get_required_env("DB_PASSWORD"),
        database=_get_required_env("DB_NAME"),
        charset="utf8mb4",
        collation="utf8mb4_unicode_ci",
        autocommit=True,
        # Connection timeouts for fast failure
        connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "5")),
    )


def get_db():
    """Get a database connection from the pool."""
    global _db_pool
    if _db_pool is None:
        with _pool_lock:
            if _db_pool is None:
                try:
                    _db_pool = _build_pool()
                except (InterfaceError, ProgrammingError, DatabaseError) as exc:
                    if isinstance(exc, DatabaseError) and getattr(exc, "errno", None) == 1130:
                        raise RuntimeError(
                            "Database host permission denied (1130). Use DB_HOST=127.0.0.1 or grant access for DB_USER at localhost in MariaDB."
                        ) from exc
                    raise RuntimeError(
                        "Database pool initialization failed. Check DB_* values in .env and verify MySQL user permissions."
                    ) from exc

    try:
        return _db_pool.get_connection()
    except (InterfaceError, ProgrammingError, DatabaseError) as exc:
        if isinstance(exc, DatabaseError) and getattr(exc, "errno", None) == 1130:
            raise RuntimeError(
                "Database host permission denied (1130). Ensure MariaDB grants allow this DB_USER from current host."
            ) from exc
        raise RuntimeError(
            "Database connection failed. Ensure MySQL is running and DB_* values in .env are correct."
        ) from exc


@contextmanager
def get_db_cursor(commit: bool = False):
    """Context manager that reuses one DB connection for all queries in a request.
    Returns a dictionary cursor.  On exit, cursor is closed and connection is
    returned to the pool unless `commit` is True, in which case conn.commit() is
    called first.
    """
    conn = getattr(_tls, 'conn', None)
    reuse = conn is not None
    if not reuse:
        conn = get_db()
        _tls.conn = conn
        _tls.conn_used = 0

    _tls.conn_used = getattr(_tls, 'conn_used', 0) + 1
    # buffered=True: fetch all rows client-side on execute so commit()/close()
    # never raise "Unread result found" (C-extension cursors are unbuffered by
    # default and leave pending results on the connection).
    cursor = conn.cursor(dictionary=True, buffered=True)
    try:
        yield cursor
        if commit:
            conn.commit()
    finally:
        # Drain any remaining rows as a belt-and-suspenders guard before close.
        try:
            cursor.fetchall()
        except Exception:
            pass
        cursor.close()
        if not reuse:
            # Return connection to pool
            _tls.conn = None
            _tls.conn_used = 0
            conn.close()


def close_request_connection():
    """Call at the end of every Flask request to clean up the thread-local connection."""
    conn = getattr(_tls, 'conn', None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _tls.conn = None
        _tls.conn_used = 0
