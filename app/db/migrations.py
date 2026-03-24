import json

from sqlalchemy import inspect, text

from app.db.database import engine


def _has_table(table_name: str) -> bool:
    return inspect(engine).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = inspect(engine)
    if not inspector.has_table(table_name):
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def run_migrations() -> None:
    statements: list[str] = []

    if not _has_table("accounts"):
        statements.append(
            """
            CREATE TABLE accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform VARCHAR(20) NOT NULL,
                phone VARCHAR(30) DEFAULT '',
                username VARCHAR(120) DEFAULT '',
                remark VARCHAR(255) DEFAULT '',
                profile_dir VARCHAR(255) DEFAULT '',
                is_default BOOLEAN DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    additions = [
        ("task_runs", "account_id", "INTEGER"),
        ("task_runs", "current_step_started_at", "DATETIME"),
        ("delivery_records", "account_id", "INTEGER"),
        ("delivery_records", "send_status", "VARCHAR(20) DEFAULT 'skipped'"),
        ("delivery_records", "chat_hr_name", "VARCHAR(120) DEFAULT ''"),
        ("rule_configs", "cross_account_dedupe_enabled", "BOOLEAN DEFAULT 0"),
        ("rule_configs", "dedupe_match_by_title", "BOOLEAN DEFAULT 1"),
        ("rule_configs", "dedupe_match_by_company", "BOOLEAN DEFAULT 1"),
    ]

    for table_name, column_name, column_type in additions:
        if not _has_column(table_name, column_name):
            statements.append(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        if _has_column("task_runs", "current_step_started_at"):
            connection.execute(
                text(
                    """
                    UPDATE task_runs
                    SET current_step_started_at = COALESCE(current_step_started_at, started_at, CURRENT_TIMESTAMP)
                    WHERE current_step_started_at IS NULL
                    """
                )
            )

    _normalize_json_columns()


def _normalize_json_columns() -> None:
    targets = [
        ("jobs", "tags"),
        ("task_logs", "extra_json"),
    ]

    with engine.begin() as connection:
        for table_name, column_name in targets:
            if not _has_column(table_name, column_name):
                continue
            rows = connection.execute(
                text(
                    f"""
                    SELECT id, {column_name}
                    FROM {table_name}
                    WHERE {column_name} IS NOT NULL
                      AND CAST({column_name} AS TEXT) LIKE '%\\u%'
                    """
                )
            ).fetchall()
            for row_id, raw_value in rows:
                if raw_value is None:
                    continue
                try:
                    parsed_value = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
                    normalized_value = json.dumps(parsed_value, ensure_ascii=False)
                except Exception:
                    continue
                connection.execute(
                    text(f"UPDATE {table_name} SET {column_name} = :value WHERE id = :row_id"),
                    {"value": normalized_value, "row_id": row_id},
                )
