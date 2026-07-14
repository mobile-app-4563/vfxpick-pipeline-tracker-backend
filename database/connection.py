import os
from threading import Lock

from mysql.connector import InterfaceError
from mysql.connector import ProgrammingError
from mysql.connector import DatabaseError
from mysql.connector import pooling
from dotenv import load_dotenv


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
load_dotenv(os.path.join(ROOT_DIR, ".env"))


_db_pool = None
_pool_lock = Lock()


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
        pool_size=10,
        host=os.getenv("DB_HOST", "localhost").strip(),
        port=int(os.getenv("DB_PORT", 3306)),
        user=_get_required_env("DB_USER"),
        password=_get_required_env("DB_PASSWORD"),
        database=_get_required_env("DB_NAME"),
        charset="utf8mb4",
        collation="utf8mb4_unicode_ci",
        autocommit=True,
    )


def get_db():
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
