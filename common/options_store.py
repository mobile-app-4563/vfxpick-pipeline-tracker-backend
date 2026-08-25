from common.db_utils import run_query


ROLE_CATEGORY = "role"
DEPARTMENT_CATEGORY = "department"
PIPELINE_DEPARTMENT_CATEGORY = "pipelineDepartment"
ARTIST_LEVEL_CATEGORY = "artistLevel"
SHOT_STATUS_CATEGORY = "shotStatus"
SUPERVISOR_STATUS_CATEGORY = "supervisorStatus"
ARTIST_STATUS_CATEGORY = "artistStatus"
BROAD_ACCESS_ROLE_CATEGORY = "broadAccessRole"


def ensure_options_table():
    run_query(
        """
        CREATE TABLE IF NOT EXISTS app_option_values (
            id INT AUTO_INCREMENT PRIMARY KEY,
            category VARCHAR(64) NOT NULL,
            value VARCHAR(120) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_category_value (category, value),
            INDEX idx_category (category)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def upsert_option(category, value):
    normalized = (value or "").strip()
    if not normalized:
        return

    ensure_options_table()
    run_query(
        """
        INSERT IGNORE INTO app_option_values (category, value)
        VALUES (%s, %s)
        """,
        (category, normalized),
    )


def seed_options(category, values):
    ensure_options_table()
    for value in values:
        upsert_option(category, value)


def list_options(category):
    ensure_options_table()
    rows = run_query(
        """
        SELECT value
        FROM app_option_values
        WHERE category = %s
        ORDER BY value
        """,
        (category,),
        fetch_all=True,
    ) or []
    return [(r.get("value") or "").strip() for r in rows if (r.get("value") or "").strip()]


def effective_pipeline_departments():
    """Static DEPARTMENTS + dynamically added pipeline department options.

    ``common.constants.DEPARTMENTS`` is a hard-coded default list, but
    departments added at runtime via ``POST /auth/departments`` only live in
    the option store.  Create/upsert validation must accept those too, so this
    helper returns the merged, de-duplicated list.
    """
    from common.constants import DEPARTMENTS

    ordered = []
    seen = set()
    for value in list(DEPARTMENTS) + list_options(PIPELINE_DEPARTMENT_CATEGORY):
        value = (value or "").strip()
        if not value or value in seen:
            continue
        ordered.append(value)
        seen.add(value)
    return ordered
