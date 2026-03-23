from collections import Counter

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.entities import DeliveryRecord, DeliveryStatus, Job, Platform, TaskLog, TaskRun, TaskStatus


def list_jobs(db: Session, platform: Platform) -> list[Job]:
    stmt = select(Job).where(Job.platform == platform.value).order_by(desc(Job.discovered_at)).limit(50)
    return list(db.scalars(stmt))


def list_deliveries(db: Session, platform: Platform) -> list[DeliveryRecord]:
    stmt = (
        select(DeliveryRecord)
        .where(DeliveryRecord.platform == platform.value)
        .order_by(desc(DeliveryRecord.created_at))
        .limit(50)
    )
    return list(db.scalars(stmt))


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
    jobs = list_jobs(db, platform)
    salary_counter: Counter[str] = Counter()
    experience_counter: Counter[str] = Counter()
    for job in jobs:
        if job.salary_min is None:
            salary_counter["未标注"] += 1
        elif job.salary_min < 10:
            salary_counter["10K以下"] += 1
        elif job.salary_min < 20:
            salary_counter["10-20K"] += 1
        elif job.salary_min < 30:
            salary_counter["20-30K"] += 1
        else:
            salary_counter["30K以上"] += 1
        experience_counter[job.experience_text or "未标注"] += 1

    return {
        "salary_buckets": [{"label": label, "value": value} for label, value in salary_counter.items()],
        "experience_buckets": [{"label": label, "value": value} for label, value in experience_counter.items()],
    }
