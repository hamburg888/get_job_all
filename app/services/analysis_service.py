from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, aliased

from app.core.time_utils import local_now
from app.models.entities import Account, DeliveryRecord, Job, Platform
from app.schemas.common import AnalysisFieldFlagsIn, AnalysisReportPackRequest
from app.services.dashboard_service import _decode_salary_text, _extract_keyword_terms, _normalize_city_bucket, _normalize_salary


@dataclass(slots=True)
class AnalysisRow:
    job: Job
    delivery: DeliveryRecord | None
    account: Account | None
    event_at: datetime
    status: str
    send_status: str | None


def _date_window_from_range(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    start = datetime.combine(date_from, time.min)
    end = datetime.combine(date_to + timedelta(days=1), time.min)
    return start, end


def _date_window(payload: AnalysisReportPackRequest) -> tuple[datetime, datetime]:
    return _date_window_from_range(payload.date_from, payload.date_to)


def _window_size_days(payload: AnalysisReportPackRequest) -> int:
    return (payload.date_to - payload.date_from).days + 1


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


def _fetch_delivery_rows(
    db: Session,
    payload: AnalysisReportPackRequest,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[AnalysisRow]:
    start, end = _date_window_from_range(date_from or payload.date_from, date_to or payload.date_to)
    stmt = (
        select(DeliveryRecord, Job, Account)
        .join(Job, Job.id == DeliveryRecord.job_id)
        .outerjoin(Account, Account.id == DeliveryRecord.account_id)
        .where(DeliveryRecord.platform == payload.platform.value)
        .where(DeliveryRecord.created_at >= start)
        .where(DeliveryRecord.created_at < end)
    )
    if payload.account_ids:
        stmt = stmt.where(DeliveryRecord.account_id.in_(payload.account_ids))

    rows = list(db.execute(stmt.order_by(desc(DeliveryRecord.created_at), desc(DeliveryRecord.id))).all())
    return [
        AnalysisRow(
            job=job,
            delivery=delivery,
            account=account,
            event_at=delivery.created_at,
            status=delivery.status,
            send_status=delivery.send_status,
        )
        for delivery, job, account in rows
    ]


def _fetch_discovery_rows(
    db: Session,
    payload: AnalysisReportPackRequest,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[AnalysisRow]:
    start, end = _date_window_from_range(date_from or payload.date_from, date_to or payload.date_to)
    latest_delivery_sq = _latest_delivery_subquery(payload.platform)
    latest_delivery = aliased(DeliveryRecord)
    account = aliased(Account)

    stmt = (
        select(Job, latest_delivery, account)
        .outerjoin(latest_delivery_sq, latest_delivery_sq.c.job_id == Job.id)
        .outerjoin(latest_delivery, latest_delivery.id == latest_delivery_sq.c.latest_delivery_id)
        .outerjoin(account, account.id == latest_delivery.account_id)
        .where(Job.platform == payload.platform.value)
        .where(Job.discovered_at >= start)
        .where(Job.discovered_at < end)
    )
    if payload.account_ids:
        stmt = stmt.where(latest_delivery.account_id.in_(payload.account_ids))

    rows = list(db.execute(stmt.order_by(desc(Job.discovered_at), desc(Job.id))).all())
    return [
        AnalysisRow(
            job=job,
            delivery=delivery,
            account=row_account,
            event_at=job.discovered_at,
            status=job.latest_status,
            send_status=delivery.send_status if delivery else None,
        )
        for job, delivery, row_account in rows
    ]


def _counter_to_buckets(counter: Counter[str], *, limit: int | None = None) -> list[dict]:
    items = [{"label": label, "value": value} for label, value in counter.most_common(limit)]
    return items


def _detect_salary_unit(job: Job) -> str:
    salary_text = _decode_salary_text(job.salary_text).lower()
    if "元/天" in salary_text or "/天" in salary_text:
        return "daily"
    if "元/月" in salary_text or "/月" in salary_text or "k" in salary_text or "万" in salary_text:
        return "monthly"
    return "unknown"


def _salary_values(job: Job) -> tuple[float | None, float | None]:
    salary_min = job.salary_min
    salary_max = job.salary_max
    if salary_min is None or salary_max is None:
        fallback_min, fallback_max = _normalize_salary(_decode_salary_text(job.salary_text) or job.salary_text)
        if salary_min is None:
            salary_min = fallback_min
        if salary_max is None:
            salary_max = fallback_max
    return salary_min, salary_max


def _monthly_salary_bucket(value: float | None) -> str:
    if value is None:
        return "未标注"
    if value < 10:
        return "10K以下"
    if value < 20:
        return "10-20K"
    if value < 30:
        return "20-30K"
    if value < 40:
        return "30-40K"
    return "40K以上"


def _daily_salary_bucket(value: float | None) -> str:
    if value is None:
        return "未标注"
    if value < 150:
        return "150元/天以下"
    if value < 250:
        return "150-250元/天"
    if value < 350:
        return "250-350元/天"
    return "350元/天以上"


def _reason_bucket(reason: str) -> str:
    normalized = (reason or "").strip()
    if not normalized:
        return "未标注"
    rules = (
        ("历史投递去重命中", "历史投递去重命中"),
        ("薪资上限过高", "薪资上限过高"),
        ("薪资下限过低", "薪资下限过低"),
        ("未命中关键词筛选", "未命中关键词筛选"),
        ("投递已超过上限", "今日投递已超过上限"),
        ("岗位已关闭", "岗位已关闭"),
        ("单次点击未触发状态变化", "点击后状态未变化"),
        ("立即沟通", "已成功立即沟通"),
        ("成功检测", "成功检测到投递完成"),
    )
    for needle, label in rules:
        if needle in normalized:
            return label

    primary = normalized.split("|", maxsplit=1)[0].strip()
    if "：" in primary:
        primary = primary.split("：", maxsplit=1)[0].strip()
    return primary or normalized[:32]


def _trim_text(value: str, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _serialize_sample(row: AnalysisRow, flags: AnalysisFieldFlagsIn, description_limit: int) -> dict:
    job = row.job
    delivery = row.delivery
    account = row.account
    payload: dict[str, object] = {
        "job_id": job.id,
        "platform_job_id": job.platform_job_id,
        "salary_unit": _detect_salary_unit(job),
    }

    if flags.title:
        payload["title"] = job.title
    if flags.company_name:
        payload["company_name"] = job.company_name
    if flags.salary_text:
        payload["salary_text"] = _decode_salary_text(job.salary_text) or job.salary_text
    if flags.city:
        payload["city"] = job.city
    if flags.experience_text:
        payload["experience_text"] = job.experience_text
    if flags.education_text:
        payload["education_text"] = job.education_text
    if flags.tags:
        payload["tags"] = list(job.tags or [])
    if flags.description:
        payload["description"] = _trim_text(job.description, description_limit)
    if flags.delivery_status:
        payload["delivery_status"] = row.status
    if flags.send_status and row.send_status is not None:
        payload["send_status"] = row.send_status
    if flags.status_reason and delivery is not None:
        payload["status_reason"] = delivery.status_reason
    if flags.account_name and account is not None:
        payload["account_id"] = account.id
        payload["account_name"] = account.username or account.phone
    if flags.discovered_at:
        payload["discovered_at"] = job.discovered_at.isoformat(timespec="seconds")
    if flags.event_at:
        payload["event_at"] = row.event_at.isoformat(timespec="seconds")
    return payload


def _take_rows(rows: list[AnalysisRow], *, limit: int, predicate=None, sort_key=None) -> list[AnalysisRow]:
    filtered = [row for row in rows if predicate(row)] if predicate else list(rows)
    if sort_key is not None:
        filtered = sorted(filtered, key=sort_key, reverse=True)
    return filtered[:limit]


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _append_flag(flags: list[dict], level: str, code: str, message: str, count: int | None = None) -> None:
    item = {"level": level, "code": code, "message": message}
    if count is not None:
        item["count"] = count
    flags.append(item)


def _build_daily_trends(rows: list[AnalysisRow]) -> list[dict]:
    grouped: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        key = row.event_at.date().isoformat()
        grouped[key]["event_count"] += 1
        grouped[key][row.status] += 1

    items: list[dict] = []
    for key in sorted(grouped.keys()):
        counter = grouped[key]
        items.append(
            {
                "date": key,
                "event_count": counter["event_count"],
                "delivered_count": counter["delivered"],
                "failed_count": counter["failed"],
                "skipped_count": counter["skipped"],
                "success_rate": _safe_ratio(counter["delivered"], counter["event_count"]),
            }
        )
    return items


def _snapshot_metrics(rows: list[AnalysisRow]) -> dict:
    status_counter = Counter(row.status for row in rows)
    unique_job_ids = {row.job.id for row in rows}
    return {
        "job_count": len(unique_job_ids),
        "event_count": len(rows),
        "delivered_count": status_counter["delivered"],
        "failed_count": status_counter["failed"],
        "skipped_count": status_counter["skipped"],
        "delivery_success_rate": _safe_ratio(status_counter["delivered"], len(rows)),
    }


def _comparison_delta(current_value: int | float, previous_value: int | float) -> dict:
    diff = current_value - previous_value
    change_rate = None
    if previous_value not in (0, 0.0):
        change_rate = round(diff / previous_value, 4)
    return {
        "current": current_value,
        "previous": previous_value,
        "diff": diff,
        "change_rate": change_rate,
    }


def _term_counter(rows: list[AnalysisRow], extractor) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        for term in extractor(row):
            counter[term] += 1
    return counter


def _build_comparison(current_rows: list[AnalysisRow], previous_rows: list[AnalysisRow], payload: AnalysisReportPackRequest) -> dict:
    if not payload.compare_previous_period:
        return {}

    current = _snapshot_metrics(current_rows)
    previous = _snapshot_metrics(previous_rows)
    current_tags = _term_counter(current_rows, lambda row: {str(tag).strip() for tag in row.job.tags or [] if str(tag).strip()})
    previous_tags = _term_counter(previous_rows, lambda row: {str(tag).strip() for tag in row.job.tags or [] if str(tag).strip()})

    shifts: list[dict] = []
    for label in set(current_tags) | set(previous_tags):
        diff = current_tags[label] - previous_tags[label]
        if diff == 0:
            continue
        shifts.append({"label": label, "current": current_tags[label], "previous": previous_tags[label], "diff": diff})
    shifts.sort(key=lambda item: (item["diff"], item["current"]), reverse=True)

    previous_date_to = payload.date_from - timedelta(days=1)
    previous_date_from = previous_date_to - timedelta(days=_window_size_days(payload) - 1)
    return {
        "enabled": True,
        "previous_period": {
            "date_from": previous_date_from.isoformat(),
            "date_to": previous_date_to.isoformat(),
            "date_basis": payload.date_basis,
        },
        "metrics": {
            "job_count": _comparison_delta(current["job_count"], previous["job_count"]),
            "event_count": _comparison_delta(current["event_count"], previous["event_count"]),
            "delivered_count": _comparison_delta(current["delivered_count"], previous["delivered_count"]),
            "failed_count": _comparison_delta(current["failed_count"], previous["failed_count"]),
            "skipped_count": _comparison_delta(current["skipped_count"], previous["skipped_count"]),
            "delivery_success_rate": _comparison_delta(current["delivery_success_rate"], previous["delivery_success_rate"]),
        },
        "top_rising_tags": [item for item in shifts if item["diff"] > 0][:10],
        "top_falling_tags": [item for item in sorted(shifts, key=lambda item: (item["diff"], item["current"])) if item["diff"] < 0][:10],
    }


def _build_term_performance(rows: list[AnalysisRow], extractor, *, minimum_count: int = 3, limit: int = 12) -> list[dict]:
    stats: dict[str, dict[str, float | int]] = {}
    for row in rows:
        salary_min, salary_max = _salary_values(row.job)
        for term in extractor(row):
            if term not in stats:
                stats[term] = {
                    "total": 0,
                    "delivered": 0,
                    "failed": 0,
                    "skipped": 0,
                    "monthly_salary_sum": 0.0,
                    "monthly_salary_count": 0,
                    "daily_salary_sum": 0.0,
                    "daily_salary_count": 0,
                }
            item = stats[term]
            item["total"] += 1
            if row.status in {"delivered", "failed", "skipped"}:
                item[row.status] += 1
            unit = _detect_salary_unit(row.job)
            if unit == "monthly" and salary_max is not None:
                item["monthly_salary_sum"] += salary_max
                item["monthly_salary_count"] += 1
            if unit == "daily" and salary_max is not None:
                item["daily_salary_sum"] += salary_max
                item["daily_salary_count"] += 1

    results: list[dict] = []
    for label, item in stats.items():
        total = int(item["total"])
        if total < minimum_count:
            continue
        monthly_count = int(item["monthly_salary_count"])
        daily_count = int(item["daily_salary_count"])
        results.append(
            {
                "label": label,
                "total": total,
                "delivered": int(item["delivered"]),
                "failed": int(item["failed"]),
                "skipped": int(item["skipped"]),
                "delivery_rate": _safe_ratio(int(item["delivered"]), total),
                "avg_monthly_salary_max": round(float(item["monthly_salary_sum"]) / monthly_count, 2) if monthly_count else None,
                "avg_daily_salary_max": round(float(item["daily_salary_sum"]) / daily_count, 2) if daily_count else None,
            }
        )
    results.sort(
        key=lambda item: (
            item["delivery_rate"],
            item["avg_monthly_salary_max"] or 0,
            item["avg_daily_salary_max"] or 0,
            item["total"],
        ),
        reverse=True,
    )
    return results[:limit]


def _build_strategy_inputs(rows: list[AnalysisRow], payload: AnalysisReportPackRequest) -> dict:
    tag_performance = _build_term_performance(
        rows,
        extractor=lambda row: {str(tag).strip() for tag in row.job.tags or [] if str(tag).strip()},
        minimum_count=3,
        limit=16,
    )
    keyword_performance = _build_term_performance(
        rows,
        extractor=lambda row: set(
            _extract_keyword_terms(
                {
                    "tags": list(row.job.tags or []),
                    "title": row.job.title,
                    "company_name": row.job.company_name,
                    "city": row.job.city,
                }
            )
        ),
        minimum_count=3,
        limit=16,
    )
    city_performance = _build_term_performance(
        rows,
        extractor=lambda row: {_normalize_city_bucket(row.job.city or "未标注")},
        minimum_count=2,
        limit=10,
    )

    high_value_rows = [
        row
        for row in rows
        if (
            (_detect_salary_unit(row.job) == "monthly" and (_salary_values(row.job)[1] or 0) >= 20)
            or (_detect_salary_unit(row.job) == "daily" and (_salary_values(row.job)[1] or 0) >= 250)
        )
    ]

    focus_directions = [
        item
        for item in tag_performance
        if item["delivery_rate"] >= 0.4 and (item["avg_monthly_salary_max"] or item["avg_daily_salary_max"] or 0) > 0
    ][:8]
    caution_directions = sorted(
        [item for item in tag_performance if item["delivery_rate"] <= 0.2 and item["total"] >= 3],
        key=lambda item: (item["delivery_rate"], -item["total"]),
    )[:8]

    return {
        "focus_directions": focus_directions,
        "caution_directions": caution_directions,
        "keyword_opportunities": [item for item in keyword_performance if item["delivery_rate"] >= 0.35][:10],
        "city_opportunities": [item for item in city_performance if item["delivery_rate"] >= 0.3][:10],
        "high_value_delivered_jobs": [
            _serialize_sample(row, payload.field_flags, payload.max_description_chars)
            for row in _take_rows(
                [row for row in high_value_rows if row.status == "delivered"],
                limit=payload.sample_limit,
                predicate=lambda current: True,
                sort_key=_salary_sort_value,
            )
        ],
        "high_value_skipped_jobs": [
            _serialize_sample(row, payload.field_flags, payload.max_description_chars)
            for row in _take_rows(
                [row for row in high_value_rows if row.status == "skipped"],
                limit=payload.sample_limit,
                predicate=lambda current: True,
                sort_key=_salary_sort_value,
            )
        ],
    }


def _build_stats(rows: list[AnalysisRow], payload: AnalysisReportPackRequest) -> tuple[dict, list[dict], list[str]]:
    status_counter: Counter[str] = Counter()
    send_status_counter: Counter[str] = Counter()
    raw_tag_counter: Counter[str] = Counter()
    keyword_counter: Counter[str] = Counter()
    city_counter: Counter[str] = Counter()
    experience_counter: Counter[str] = Counter()
    education_counter: Counter[str] = Counter()
    company_counter: Counter[str] = Counter()
    salary_unit_counter: Counter[str] = Counter()
    monthly_salary_counter: Counter[str] = Counter()
    daily_salary_counter: Counter[str] = Counter()
    skip_reason_counter: Counter[str] = Counter()
    failure_reason_counter: Counter[str] = Counter()
    account_stats: dict[tuple[int | None, str], dict[str, object]] = {}
    unique_job_ids: set[int] = set()
    suspicious_salary_count = 0
    truncated_description_count = 0
    mixed_units = False

    for row in rows:
        job = row.job
        unique_job_ids.add(job.id)
        status_counter[row.status] += 1
        if row.send_status:
            send_status_counter[row.send_status] += 1
        company_counter[job.company_name or "未标注"] += 1
        experience_counter[(job.experience_text or "未标注").strip() or "未标注"] += 1
        education_counter[(job.education_text or "未标注").strip() or "未标注"] += 1
        city_counter[_normalize_city_bucket(job.city or "未标注")] += 1
        for tag in job.tags or []:
            normalized_tag = str(tag).strip()
            if normalized_tag:
                raw_tag_counter[normalized_tag] += 1

        serialized_job = {
            "tags": list(job.tags or []),
            "title": job.title,
            "company_name": job.company_name,
            "city": job.city,
        }
        for keyword in _extract_keyword_terms(serialized_job):
            keyword_counter[keyword] += 1

        if len((job.description or "").strip()) > payload.max_description_chars:
            truncated_description_count += 1

        salary_unit = _detect_salary_unit(job)
        salary_unit_counter[salary_unit] += 1
        salary_min, salary_max = _salary_values(job)
        if salary_unit == "monthly":
            monthly_salary_counter[_monthly_salary_bucket(salary_min)] += 1
            if (salary_min and salary_min > 80) or (salary_max and salary_max > 80):
                suspicious_salary_count += 1
        elif salary_unit == "daily":
            daily_salary_counter[_daily_salary_bucket(salary_min)] += 1
            if (salary_min and salary_min > 800) or (salary_max and salary_max > 800):
                suspicious_salary_count += 1

        account_key = (
            row.account.id if row.account else None,
            (row.account.username or row.account.phone) if row.account else "未关联账号",
        )
        if account_key not in account_stats:
            account_stats[account_key] = {
                "account_id": account_key[0],
                "account_name": account_key[1],
                "total": 0,
                "delivered": 0,
                "failed": 0,
                "skipped": 0,
            }
        account_stats[account_key]["total"] += 1
        if row.status in {"delivered", "failed", "skipped"}:
            account_stats[account_key][row.status] += 1

        if row.delivery is not None and row.status == "skipped":
            skip_reason_counter[_reason_bucket(row.delivery.status_reason)] += 1
        if row.delivery is not None and row.status == "failed":
            failure_reason_counter[_reason_bucket(row.delivery.status_reason)] += 1

    mixed_units = salary_unit_counter["monthly"] > 0 and salary_unit_counter["daily"] > 0

    account_performance: list[dict] = []
    for item in sorted(account_stats.values(), key=lambda value: (value["total"], value["delivered"]), reverse=True):
        total = int(item["total"])
        delivered = int(item["delivered"])
        account_performance.append(
            {
                **item,
                "success_rate": _safe_ratio(delivered, total),
            }
        )

    flags: list[dict] = []
    notes: list[str] = []
    if not rows:
        _append_flag(flags, "warning", "empty_result", "当前筛选范围内没有可分析的数据。", 0)
    if len(rows) < 10:
        _append_flag(flags, "warning", "low_sample_size", "样本量较小，建议将日期范围扩展到最近 3 到 7 天后再看趋势。", len(rows))
    if mixed_units:
        _append_flag(flags, "info", "mixed_salary_units", "样本同时包含月薪和日薪岗位，薪资对比应按口径分别解读。")
    if suspicious_salary_count:
        _append_flag(flags, "warning", "suspicious_salary_values", "发现疑似异常薪资值，建议先核对原始薪资文本再做高薪结论。", suspicious_salary_count)
    if truncated_description_count:
        _append_flag(flags, "info", "description_truncated", "为控制 token，部分岗位描述已截断，分析时应以摘要为准。", truncated_description_count)
    if _window_size_days(payload) > 14:
        _append_flag(flags, "info", "wide_date_window", "当前分析窗口超过 14 天，更适合做阶段复盘而不是日报。", _window_size_days(payload))

    if not payload.account_ids:
        notes.append("本次报告聚合了该平台下全部账号的数据。")
    if payload.date_basis == "discovery" and payload.account_ids:
        notes.append("发现口径下的账号筛选基于岗位最近一次投递记录对应的账号。")
    if payload.compare_previous_period:
        notes.append("已附带上一等长时间窗口的对比数据，可直接用于日报和策略复盘。")

    stats = {
        "job_count": len(unique_job_ids),
        "event_count": len(rows),
        "delivered_count": status_counter["delivered"],
        "failed_count": status_counter["failed"],
        "skipped_count": status_counter["skipped"],
        "delivery_success_rate": _safe_ratio(status_counter["delivered"], len(rows)),
        "status_buckets": _counter_to_buckets(status_counter),
        "send_status_buckets": _counter_to_buckets(send_status_counter),
        "top_tags": _counter_to_buckets(raw_tag_counter, limit=16),
        "top_keywords": _counter_to_buckets(keyword_counter, limit=16),
        "city_buckets": _counter_to_buckets(city_counter, limit=12),
        "experience_buckets": _counter_to_buckets(experience_counter, limit=12),
        "education_buckets": _counter_to_buckets(education_counter, limit=12),
        "top_companies": _counter_to_buckets(company_counter, limit=12),
        "salary_unit_buckets": _counter_to_buckets(salary_unit_counter),
        "monthly_salary_buckets": _counter_to_buckets(monthly_salary_counter),
        "daily_salary_buckets": _counter_to_buckets(daily_salary_counter),
        "top_skip_reasons": _counter_to_buckets(skip_reason_counter, limit=10),
        "top_failure_reasons": _counter_to_buckets(failure_reason_counter, limit=10),
        "account_performance": account_performance,
        "daily_trends": _build_daily_trends(rows),
    }
    return stats, flags, notes


def _salary_sort_value(row: AnalysisRow) -> float:
    salary_min, salary_max = _salary_values(row.job)
    return salary_max or salary_min or 0


def _tag_density(row: AnalysisRow) -> int:
    return len(row.job.tags or [])


def _serialize_samples(rows: list[AnalysisRow], payload: AnalysisReportPackRequest) -> dict[str, list[dict]]:
    flags = payload.field_flags
    limit = payload.sample_limit
    description_limit = payload.max_description_chars
    return {
        "recent_jobs": [
            _serialize_sample(row, flags, description_limit)
            for row in _take_rows(rows, limit=limit, predicate=lambda current: True)
        ],
        "delivered_jobs": [
            _serialize_sample(row, flags, description_limit)
            for row in _take_rows(rows, limit=limit, predicate=lambda current: current.status == "delivered")
        ],
        "failed_jobs": [
            _serialize_sample(row, flags, description_limit)
            for row in _take_rows(rows, limit=limit, predicate=lambda current: current.status == "failed")
        ],
        "skipped_jobs": [
            _serialize_sample(row, flags, description_limit)
            for row in _take_rows(rows, limit=limit, predicate=lambda current: current.status == "skipped")
        ],
        "high_monthly_salary_jobs": [
            _serialize_sample(row, flags, description_limit)
            for row in _take_rows(
                rows,
                limit=limit,
                predicate=lambda current: _detect_salary_unit(current.job) == "monthly",
                sort_key=_salary_sort_value,
            )
        ],
        "high_daily_salary_jobs": [
            _serialize_sample(row, flags, description_limit)
            for row in _take_rows(
                rows,
                limit=limit,
                predicate=lambda current: _detect_salary_unit(current.job) == "daily",
                sort_key=_salary_sort_value,
            )
        ],
        "tag_dense_jobs": [
            _serialize_sample(row, flags, description_limit)
            for row in _take_rows(rows, limit=limit, predicate=lambda current: True, sort_key=_tag_density)
        ],
    }


def build_analysis_report_pack(db: Session, payload: AnalysisReportPackRequest) -> dict:
    rows = _fetch_delivery_rows(db, payload) if payload.date_basis == "delivery" else _fetch_discovery_rows(db, payload)
    stats, data_quality_flags, notes = _build_stats(rows, payload)

    selected_accounts: list[dict] = []
    missing_account_ids: list[int] = []
    if payload.account_ids:
        account_stmt = select(Account).where(Account.id.in_(payload.account_ids)).order_by(Account.id)
        selected_accounts = [
            {
                "account_id": account.id,
                "platform": account.platform,
                "account_name": account.username or account.phone,
            }
            for account in db.scalars(account_stmt)
        ]
        found_ids = {item["account_id"] for item in selected_accounts}
        missing_account_ids = [account_id for account_id in payload.account_ids if account_id not in found_ids]
        if missing_account_ids:
            _append_flag(
                data_quality_flags,
                "warning",
                "unknown_account_ids",
                "部分账号 ID 不存在，已按可识别账号继续生成报告。",
                len(missing_account_ids),
            )

    comparison: dict = {}
    if payload.compare_previous_period:
        previous_date_to = payload.date_from - timedelta(days=1)
        previous_date_from = previous_date_to - timedelta(days=_window_size_days(payload) - 1)
        previous_rows = (
            _fetch_delivery_rows(db, payload, date_from=previous_date_from, date_to=previous_date_to)
            if payload.date_basis == "delivery"
            else _fetch_discovery_rows(db, payload, date_from=previous_date_from, date_to=previous_date_to)
        )
        comparison = _build_comparison(rows, previous_rows, payload)
        if not previous_rows:
            _append_flag(
                data_quality_flags,
                "info",
                "previous_period_empty",
                "上一对比窗口内没有匹配数据，本次仅能输出当前窗口分析。",
                0,
            )

    meta = {
        "generated_at": local_now().isoformat(timespec="seconds"),
        "platform": payload.platform.value,
        "report_type": payload.report_type,
        "date_basis": payload.date_basis,
        "date_from": payload.date_from.isoformat(),
        "date_to": payload.date_to.isoformat(),
        "window_days": _window_size_days(payload),
        "compare_previous_period": payload.compare_previous_period,
        "account_ids": payload.account_ids,
        "selected_accounts": selected_accounts,
        "missing_account_ids": missing_account_ids,
        "focus": payload.focus,
        "sample_limit": payload.sample_limit,
    }

    return {
        "meta": meta,
        "field_flags": payload.field_flags.model_dump(),
        "stats": stats,
        "comparison": comparison,
        "strategy_inputs": _build_strategy_inputs(rows, payload),
        "samples": _serialize_samples(rows, payload),
        "data_quality_flags": data_quality_flags,
        "notes": notes,
    }
