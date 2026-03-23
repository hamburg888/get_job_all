from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.entities import Platform
from app.schemas.common import (
    ConfirmLoginRequest,
    DeliveryOut,
    JobOut,
    LogOut,
    OverviewOut,
    StartTaskRequest,
    StatsOut,
    StopTaskRequest,
    TaskStatusResponse,
)
from app.services.dashboard_service import get_overview, get_stats, list_deliveries, list_jobs, list_logs
from app.services.task_runner import task_runner

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/dashboard/overview/{platform}", response_model=OverviewOut)
def dashboard_overview(platform: Platform, db: Session = Depends(get_db)) -> dict:
    return get_overview(db, platform)


@router.get("/dashboard/jobs/{platform}", response_model=list[JobOut])
def dashboard_jobs(platform: Platform, db: Session = Depends(get_db)) -> list:
    return list_jobs(db, platform)


@router.get("/dashboard/deliveries/{platform}", response_model=list[DeliveryOut])
def dashboard_deliveries(platform: Platform, db: Session = Depends(get_db)) -> list:
    return list_deliveries(db, platform)


@router.get("/dashboard/logs/{platform}", response_model=list[LogOut])
def dashboard_logs(platform: Platform, limit: int = 40, db: Session = Depends(get_db)) -> list:
    return list_logs(db, platform, limit)


@router.get("/dashboard/stats/{platform}", response_model=StatsOut)
def dashboard_stats(platform: Platform, db: Session = Depends(get_db)) -> dict:
    return get_stats(db, platform)


@router.get("/tasks/status/{platform}", response_model=TaskStatusResponse)
def get_task_status(platform: Platform) -> TaskStatusResponse:
    return task_runner.status(platform)


@router.post("/tasks/start", response_model=TaskStatusResponse)
def start_task(payload: StartTaskRequest) -> TaskStatusResponse:
    return task_runner.start(payload)


@router.post("/tasks/stop", response_model=TaskStatusResponse)
def stop_task(payload: StopTaskRequest) -> TaskStatusResponse:
    return task_runner.stop(payload.platform)


@router.post("/tasks/confirm-login", response_model=TaskStatusResponse)
def confirm_login(payload: ConfirmLoginRequest) -> TaskStatusResponse:
    return task_runner.confirm_login(payload.platform)
