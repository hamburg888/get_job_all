from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
import re

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, aliased

from app.models.entities import Account, DeliveryRecord, DeliveryStatus, Job, Platform, TaskLog, TaskRun, TaskStatus

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

KEYWORD_STOP_WORDS = {
    "招聘",
    "岗位",
    "职位",
    "有限公司",
    "有限责任公司",
    "公司",
    "集团",
    "科技",
    "信息",
    "工程师",
    "开发",
    "高级",
    "中级",
    "初级",
    "广州",
    "boss",
    "直聘",
}

TOKEN_SPLIT_RE = re.compile(r"[\s/|,，、·:：;；()（）\[\]【】<>《》+\-]+")
GENERIC_TAG_RE = re.compile(
    r"(经验|学历|不限|应届|\d+\s*-\s*\d+\s*年|\d+\s*年|本科|大专|专科|硕士|博士|中专|高中|初中|\d+\s*天(?:/周)?|\d+\s*个月)"
)


@dataclass(slots=True)
class DashboardFilters:
    account_id: int | None = None
    statuses: tuple[str, ...] = ()
    send_statuses: tuple[str, ...] = ()
    date_from: str | None = None
    date_to: str | None = None


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


def _parse_filter_dates(filters: DashboardFilters | None) -> tuple[datetime | None, datetime | None]:
    if not filters:
        return None, None
    start: datetime | None = None
    end: datetime | None = None
    if filters.date_from:
        try:
            start = datetime.fromisoformat(filters.date_from.strip())
        except ValueError:
            start = None
    if filters.date_to:
        try:
            end = datetime.fromisoformat(filters.date_to.strip()) + timedelta(days=1)
        except ValueError:
            end = None
    return start, end


def _latest_delivery_subquery(platform: Platform):
    return (
        select(
            DeliveryRecord.job_id.label("job_id"),
            func.max(DeliveryRecord.id).label("latest_delivery_id"),
        )
        .where(DeliveryRecord.platform == platform.value)
        .group_by(DeliveryRecord.job_id)
        .subquery()
    )


def _build_job_rows_query(platform: Platform, filters: DashboardFilters | None = None):
    normalized_filters = filters or DashboardFilters()
    latest_delivery_sq = _latest_delivery_subquery(platform)
    latest_delivery = aliased(DeliveryRecord)
    account = aliased(Account)
    stmt = (
        select(Job, latest_delivery, account)
        .outerjoin(latest_delivery_sq, latest_delivery_sq.c.job_id == Job.id)
        .outerjoin(latest_delivery, latest_delivery.id == latest_delivery_sq.c.latest_delivery_id)
        .outerjoin(account, account.id == latest_delivery.account_id)
        .where(Job.platform == platform.value)
    )
    if normalized_filters.account_id is not None:
        stmt = stmt.where(latest_delivery.account_id == normalized_filters.account_id)
    if normalized_filters.statuses:
        stmt = stmt.where(Job.latest_status.in_(normalized_filters.statuses))
    if normalized_filters.send_statuses:
        stmt = stmt.where(latest_delivery.send_status.in_(normalized_filters.send_statuses))
    date_start, date_end = _parse_filter_dates(normalized_filters)
    if date_start is not None:
        stmt = stmt.where(latest_delivery.created_at >= date_start)
    if date_end is not None:
        stmt = stmt.where(latest_delivery.created_at < date_end)
    return stmt.order_by(desc(Job.discovered_at))


def _serialize_job_rows(rows: list[tuple[Job, DeliveryRecord | None, Account | None]]) -> list[dict]:
    items: list[dict] = []
    for job, latest_delivery, account in rows:
        salary_text = _decode_salary_text(job.salary_text)
        salary_min = job.salary_min
        salary_max = job.salary_max
        if salary_min is None or salary_max is None:
            fallback_min, fallback_max = _normalize_salary(salary_text or job.salary_text)
            if salary_min is None:
                salary_min = fallback_min
            if salary_max is None:
                salary_max = fallback_max
        items.append(
            {
                "id": job.id,
                "platform": Platform(job.platform),
                "platform_job_id": job.platform_job_id,
                "account_id": latest_delivery.account_id if latest_delivery else None,
                "account_name": (account.username or account.phone) if account else None,
                "title": job.title,
                "company_name": job.company_name,
                "salary_text": salary_text or job.salary_text,
                "salary_min": salary_min,
                "salary_max": salary_max,
                "city": job.city,
                "experience_text": job.experience_text,
                "education_text": job.education_text,
                "description": job.description,
                "detail_url": job.detail_url,
                "tags": job.tags,
                "latest_status": job.latest_status,
                "discovered_at": job.discovered_at,
                "delivered_at": latest_delivery.created_at if latest_delivery else None,
                "latest_reason": latest_delivery.status_reason if latest_delivery else None,
                "latest_send_status": latest_delivery.send_status if latest_delivery else None,
                "latest_chat_message": latest_delivery.chat_message if latest_delivery else None,
            }
        )
    return items


def _bucket_counter(counter: Counter[str], *, limit: int | None = None) -> list[dict]:
    items = [{"label": label, "value": value} for label, value in counter.most_common(limit)]
    return items


def _extract_keyword_terms(job: dict) -> list[str]:
    terms: list[str] = []
    raw_values = [*(job.get("tags") or [])]
    for value in raw_values:
        normalized = re.sub(r"\s+", " ", str(value)).strip()
        if not normalized:
            continue
        if GENERIC_TAG_RE.search(normalized):
            continue
        if len(normalized) <= 18 and normalized not in KEYWORD_STOP_WORDS:
            terms.append(normalized)
        for token in TOKEN_SPLIT_RE.split(normalized):
            token = token.strip()
            if not token:
                continue
            if token.lower() in KEYWORD_STOP_WORDS or token in KEYWORD_STOP_WORDS:
                continue
            if GENERIC_TAG_RE.search(token):
                continue
            if len(token) < 2:
                continue
            if re.fullmatch(r"[A-Za-z]{1,2}", token):
                continue
            terms.append(token)
    return terms


def _normalize_city_bucket(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value or "").strip()
    if not normalized:
        return "未标注"
    dot_parts = [part.strip() for part in normalized.split("·") if part.strip()]
    if len(dot_parts) >= 2:
        return "·".join(dot_parts[:2])
    dash_parts = [part.strip() for part in re.split(r"[-/]", normalized) if part.strip()]
    if len(dash_parts) >= 2:
        return "-".join(dash_parts[:2])
    return normalized


def list_jobs(db: Session, platform: Platform, page: int = 1, page_size: int = 10, filters: DashboardFilters | None = None) -> dict:
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    base_stmt = _build_job_rows_query(platform, filters)
    total = db.scalar(select(func.count()).select_from(base_stmt.order_by(None).subquery())) or 0
    rows = list(db.execute(base_stmt.offset((page - 1) * page_size).limit(page_size)).all())
    return {
        "items": _serialize_job_rows(rows),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def list_deliveries(db: Session, platform: Platform, page: int = 1, page_size: int = 10, filters: DashboardFilters | None = None) -> dict:
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    normalized_filters = filters or DashboardFilters()
    date_start, date_end = _parse_filter_dates(normalized_filters)

    def _apply_delivery_filters(stmt):
        if normalized_filters.account_id is not None:
            stmt = stmt.where(DeliveryRecord.account_id == normalized_filters.account_id)
        if normalized_filters.statuses:
            stmt = stmt.where(DeliveryRecord.status.in_(normalized_filters.statuses))
        if normalized_filters.send_statuses:
            stmt = stmt.where(DeliveryRecord.send_status.in_(normalized_filters.send_statuses))
        if date_start is not None:
            stmt = stmt.where(DeliveryRecord.created_at >= date_start)
        if date_end is not None:
            stmt = stmt.where(DeliveryRecord.created_at < date_end)
        return stmt

    base_stmt = _apply_delivery_filters(select(DeliveryRecord).where(DeliveryRecord.platform == platform.value))
    total = db.scalar(select(func.count()).select_from(base_stmt.subquery())) or 0
    stmt = _apply_delivery_filters(
        select(DeliveryRecord, Account)
        .join(Account, DeliveryRecord.account_id == Account.id, isouter=True)
        .where(DeliveryRecord.platform == platform.value)
        .order_by(desc(DeliveryRecord.created_at))
    ).offset((page - 1) * page_size).limit(page_size)
    return {
        "items": [
            {
                "id": delivery.id,
                "platform": platform,
                "account_id": delivery.account_id,
                "account_name": (account.username or account.phone) if account else None,
                "status": delivery.status,
                "status_reason": delivery.status_reason,
                "send_status": delivery.send_status,
                "chat_sent": delivery.chat_sent,
                "chat_message": delivery.chat_message,
                "created_at": delivery.created_at,
                "job_id": delivery.job_id,
            }
            for delivery, account in db.execute(stmt)
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def list_logs(db: Session, platform: Platform, limit: int = 40, filters: DashboardFilters | None = None) -> list[TaskLog]:
    normalized_filters = filters or DashboardFilters()
    stmt = select(TaskLog).where(TaskLog.platform == platform.value)
    if normalized_filters.account_id is not None:
        stmt = stmt.join(TaskRun, TaskRun.id == TaskLog.task_run_id).where(TaskRun.account_id == normalized_filters.account_id)
    date_start, date_end = _parse_filter_dates(normalized_filters)
    if date_start is not None:
        stmt = stmt.where(TaskLog.created_at >= date_start)
    if date_end is not None:
        stmt = stmt.where(TaskLog.created_at < date_end)
    stmt = stmt.order_by(desc(TaskLog.created_at)).limit(limit)
    return list(db.scalars(stmt))


def get_overview(db: Session, platform: Platform, filters: DashboardFilters | None = None) -> dict:
    filtered_jobs = _build_job_rows_query(platform, filters).order_by(None).subquery()
    total_jobs = db.scalar(select(func.count()).select_from(filtered_jobs)) or 0
    delivered = (
        db.scalar(
            select(func.count()).select_from(filtered_jobs).where(filtered_jobs.c.latest_status == DeliveryStatus.DELIVERED.value)
        )
        or 0
    )
    failed = (
        db.scalar(
            select(func.count()).select_from(filtered_jobs).where(filtered_jobs.c.latest_status == DeliveryStatus.FAILED.value)
        )
        or 0
    )

    task_stmt = select(TaskRun).where(TaskRun.platform == platform.value)
    normalized_filters = filters or DashboardFilters()
    if normalized_filters.account_id is not None:
        task_stmt = task_stmt.where(TaskRun.account_id == normalized_filters.account_id)
    date_start, date_end = _parse_filter_dates(normalized_filters)
    if date_start is not None:
        task_stmt = task_stmt.where(TaskRun.started_at >= date_start)
    if date_end is not None:
        task_stmt = task_stmt.where(TaskRun.started_at < date_end)
    last_task = db.scalar(task_stmt.order_by(desc(TaskRun.started_at)).limit(1))

    return {
        "platform": platform,
        "total_jobs": total_jobs,
        "delivered_jobs": delivered,
        "failed_jobs": failed,
        "active_task_status": TaskStatus(last_task.status) if last_task else TaskStatus.IDLE,
        "last_run_at": last_task.started_at if last_task else None,
    }


def get_stats(db: Session, platform: Platform, filters: DashboardFilters | None = None) -> dict:
    rows = list(db.execute(_build_job_rows_query(platform, filters).limit(800)).all())
    serialized_jobs = _serialize_job_rows(rows)
    salary_counter: Counter[str] = Counter()
    experience_counter: Counter[str] = Counter()
    city_counter: Counter[str] = Counter()
    keyword_counter: Counter[str] = Counter()

    for job in serialized_jobs:
        salary_min = job.get("salary_min")
        experience_text = job.get("experience_text") or "未标注"
        city_text = _normalize_city_bucket(job.get("city") or "未标注")
        if salary_min is None:
            salary_counter["未标注"] += 1
        elif salary_min < 10:
            salary_counter["10K以下"] += 1
        elif salary_min < 20:
            salary_counter["10-20K"] += 1
        elif salary_min < 30:
            salary_counter["20-30K"] += 1
        else:
            salary_counter["30K以上"] += 1
        experience_counter[experience_text] += 1
        city_counter[city_text] += 1
        for term in _extract_keyword_terms(job):
            keyword_counter[term] += 1

    return {
        "salary_buckets": _bucket_counter(salary_counter),
        "experience_buckets": _bucket_counter(experience_counter),
        "city_buckets": _bucket_counter(city_counter, limit=12),
        "keyword_buckets": _bucket_counter(keyword_counter, limit=36),
    }
