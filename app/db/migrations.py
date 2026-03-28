import json
import re

from sqlalchemy import inspect, text

from app.db.database import engine

BOSS_SALARY_FONT_MAP = str.maketrans(
    {
        ord("\uE030"): "0",
        ord("\uE031"): "1",
        ord("\uE032"): "2",
        ord("\uE033"): "3",
        ord("\uE034"): "4",
        ord("\uE035"): "5",
        ord("\uE036"): "6",
        ord("\uE037"): "7",
        ord("\uE038"): "8",
        ord("\uE039"): "9",
        ord("\uE03A"): "10",
        ord("\uE8F0"): "0",
        ord("\uE8F1"): "1",
        ord("\uE8F2"): "2",
        ord("\uE8F3"): "3",
        ord("\uE8F4"): "4",
        ord("\uE8F5"): "5",
        ord("\uE8F6"): "6",
        ord("\uE8F7"): "7",
        ord("\uE8F8"): "8",
        ord("\uE8F9"): "9",
    }
)


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
        ("rule_configs", "enable_company_blacklist", "BOOLEAN DEFAULT 0"),
        ("rule_configs", "company_blacklist", "TEXT DEFAULT ''"),
        ("jobs", "education_text", "VARCHAR(80) DEFAULT ''"),
    ]

    for table_name, column_name, column_type in additions:
        if not _has_table(table_name):
            continue
        if not _has_column(table_name, column_name):
            statements.append(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    if statements:
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
    _backfill_job_salary_fields()


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


def _decode_salary_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized.translate(BOSS_SALARY_FONT_MAP)


def _parse_salary_part(value: str) -> float | None:
    digits = re.findall(r"\d+(?:\.\d+)?", value)
    if not digits:
        return None
    number = float(digits[0])
    normalized = value.lower()
    if "w" in normalized:
        return number * 10
    if "k" in normalized:
        return number
    return number / 1000 if number > 1000 else number


def _normalize_salary(raw_salary: str) -> tuple[float | None, float | None]:
    salary = _decode_salary_text(raw_salary).replace("元", "").replace("万", "w").replace("K", "k").strip()
    if not salary:
        return None, None
    if "-" not in salary:
        value = _parse_salary_part(salary)
        return value, value
    left, right = salary.split("-", maxsplit=1)
    left, right = _inherit_range_units(left, right)
    return _parse_salary_part(left), _parse_salary_part(right)


def _inherit_range_units(left: str, right: str) -> tuple[str, str]:
    unit_pattern = re.compile(r"[wk]", re.IGNORECASE)
    left_unit_match = unit_pattern.search(left)
    right_unit_match = unit_pattern.search(right)
    left_unit = left_unit_match.group(0).lower() if left_unit_match else ""
    right_unit = right_unit_match.group(0).lower() if right_unit_match else ""

    if not left_unit and right_unit:
        left = f"{left}{right_unit}"
    elif left_unit and not right_unit:
        right = f"{right}{left_unit}"
    return left, right


def _backfill_job_salary_fields() -> None:
    required_columns = ("salary_text", "salary_min", "salary_max")
    if not _has_table("jobs") or any(not _has_column("jobs", column) for column in required_columns):
        return

    with engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, salary_text, salary_min, salary_max
                FROM jobs
                WHERE salary_text IS NOT NULL
                  AND salary_text != ''
                """
            )
        ).fetchall()
        for row_id, salary_text, salary_min, salary_max in rows:
            decoded_text = _decode_salary_text(salary_text)
            parsed_min, parsed_max = _normalize_salary(decoded_text or salary_text or "")
            next_min = parsed_min if parsed_min is not None else salary_min
            next_max = parsed_max if parsed_max is not None else salary_max
            if (
                decoded_text == salary_text
                and next_min == salary_min
                and next_max == salary_max
            ):
                continue
            connection.execute(
                text(
                    """
                    UPDATE jobs
                    SET salary_text = :salary_text,
                        salary_min = :salary_min,
                        salary_max = :salary_max
                    WHERE id = :row_id
                    """
                ),
                {
                    "salary_text": decoded_text or salary_text,
                    "salary_min": next_min,
                    "salary_max": next_max,
                    "row_id": row_id,
                },
            )
