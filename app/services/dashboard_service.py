from collections import Counter

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.entities import Account, DeliveryRecord, DeliveryStatus, Job, Platform, TaskLog, TaskRun, TaskStatus


def _serialize_jobs(db: Session, jobs: list[Job]) -> list[dict]:
    job_ids = [job.id for job in jobs]
    latest_delivery_map: dict[int, DeliveryRecord] = {}
    account_map: dict[int, Account] = {}
    if job_ids:
        delivery_stmt = (
            select(DeliveryRecord)
            .where(DeliveryRecord.job_id.in_(job_ids))
            .order_by(desc(DeliveryRecord.created_at))
        )
        for delivery in db.scalars(delivery_stmt):
            latest_delivery_map.setdefault(delivery.job_id, delivery)
        account_ids = {
            delivery.account_id
            for delivery in latest_delivery_map.values()
            if delivery.account_id is not None
        }
        if account_ids:
            account_stmt = select(Account).where(Account.id.in_(account_ids))
            account_map = {account.id: account for account in db.scalars(account_stmt)}

    items = []
    for job in jobs:
        latest_delivery = latest_delivery_map.get(job.id)
        account = account_map.get(latest_delivery.account_id) if latest_delivery and latest_delivery.account_id else None
        items.append(
            {
                "id": job.id,
                "platform": Platform(job.platform),
                "platform_job_id": job.platform_job_id,
                "account_id": latest_delivery.account_id if latest_delivery else None,
                "account_name": (account.username or account.phone) if account else None,
                "title": job.title,
                "company_name": job.company_name,
                "salary_text": job.salary_text,
                "salary_min": job.salary_min,
                "salary_max": job.salary_max,
                "city": job.city,
                "experience_text": job.experience_text,
                "detail_url": job.detail_url,
                "tags": job.tags,
                "latest_status": job.latest_status,
                "discovered_at": job.discovered_at,
                "delivered_at": latest_delivery.created_at if latest_delivery else None,
                "latest_reason": latest_delivery.status_reason if latest_delivery else None,
            }
        )
    return items


def list_jobs(db: Session, platform: Platform, page: int = 1, page_size: int = 10) -> dict:
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    total = db.scalar(select(func.count(Job.id)).where(Job.platform == platform.value)) or 0
    stmt = (
        select(Job)
        .where(Job.platform == platform.value)
        .order_by(desc(Job.discovered_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    jobs = list(db.scalars(stmt))
    return {
        "items": _serialize_jobs(db, jobs),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def list_deliveries(db: Session, platform: Platform, page: int = 1, page_size: int = 10) -> dict:
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    total = db.scalar(select(func.count(DeliveryRecord.id)).where(DeliveryRecord.platform == platform.value)) or 0
    stmt = (
        select(DeliveryRecord, Account)
        .join(Account, DeliveryRecord.account_id == Account.id, isouter=True)
        .where(DeliveryRecord.platform == platform.value)
        .order_by(desc(DeliveryRecord.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [
            {
                "id": delivery.id,
                "platform": platform,
                "account_id": delivery.account_id,
                "account_name": (account.username or account.phone) if account else None,
                "status": delivery.status,
                "status_reason": delivery.status_reason,
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


def list_logs(db: Session, platform: Platform, limit: int = 40) -> list[TaskLog]:
    stmt = select(TaskLog).where(TaskLog.platform == platform.value).order_by(desc(TaskLog.created_at)).limit(limit)
    return list(db.scalars(stmt))


def get_overview(db: Session, platform: Platform) -> dict:
    total_jobs = db.scalar(select(func.count(Job.id)).where(Job.platform == platform.value)) or 0
    delivered = (
        db.scalar(
            select(func.count(DeliveryRecord.id)).where(
                DeliveryRecord.platform == platform.value, DeliveryRecord.status == DeliveryStatus.DELIVERED.value
            )
        )
        or 0
    )
    failed = (
        db.scalar(
            select(func.count(DeliveryRecord.id)).where(
                DeliveryRecord.platform == platform.value, DeliveryRecord.status == DeliveryStatus.FAILED.value
            )
        )
        or 0
    )
    last_task = db.scalar(
        select(TaskRun).where(TaskRun.platform == platform.value).order_by(desc(TaskRun.started_at)).limit(1)
    )
    return {
        "platform": platform,
        "total_jobs": total_jobs,
        "delivered_jobs": delivered,
        "failed_jobs": failed,
        "active_task_status": TaskStatus(last_task.status) if last_task else TaskStatus.IDLE,
        "last_run_at": last_task.started_at if last_task else None,
    }


def get_stats(db: Session, platform: Platform) -> dict:
    jobs = list(
        db.scalars(select(Job).where(Job.platform == platform.value).order_by(desc(Job.discovered_at)).limit(500))
    )
    serialized_jobs = _serialize_jobs(db, jobs)
    salary_counter: Counter[str] = Counter()
    experience_counter: Counter[str] = Counter()
    city_counter: Counter[str] = Counter()
    for job in serialized_jobs:
        salary_min = job.get("salary_min")
        experience_text = job.get("experience_text") or "未标注"
        city_text = job.get("city") or "未标注"
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

    return {
        "salary_buckets": [{"label": label, "value": value} for label, value in salary_counter.items()],
        "experience_buckets": [{"label": label, "value": value} for label, value in experience_counter.items()],
        "city_buckets": [{"label": label, "value": value} for label, value in city_counter.items()],
    }
