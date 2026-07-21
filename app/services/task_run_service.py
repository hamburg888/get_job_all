from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.entities import Account, TaskRun


def list_task_runs(db: Session, platform: str | None = None, limit: int = 50) -> list[dict]:
    stmt = select(TaskRun, Account).join(Account, TaskRun.account_id == Account.id, isouter=True)
    if platform:
        stmt = stmt.where(TaskRun.platform == platform)
    stmt = stmt.order_by(desc(TaskRun.started_at)).limit(max(1, min(limit, 200)))
    rows = db.execute(stmt).all()
    out: list[dict] = []
    for task, account in rows:
        out.append({
            "id": task.id,
            "platform": task.platform,
            "keyword": task.keyword or "",
            "status": task.status or "",
            "config_json": task.config_json or {},
            "processed_count": task.processed_count or 0,
            "delivered_count": task.delivered_count or 0,
            "failed_count": task.failed_count or 0,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "account_name": (account.username or account.phone) if account else None,
        })
    return out
