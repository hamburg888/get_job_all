from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.entities import Platform
from app.schemas.common import (
    AccountCreate,
    AccountOut,
    AccountSetDefaultRequest,
    AccountUpdate,
    BrowserConfigSessionOut,
    ConfirmLoginRequest,
    DeliveryOut,
    JobOut,
    LogOut,
    OverviewOut,
    PaginatedDeliveriesOut,
    PaginatedJobsOut,
    StartTaskRequest,
    StatsOut,
    StopTaskRequest,
    TaskStatusResponse,
)
from app.services.account_browser_service import account_browser_service
from app.services.account_service import create_account, get_account, list_accounts, set_default_account, update_account
from app.services.dashboard_service import get_overview, get_stats, list_deliveries, list_jobs, list_logs
from app.services.task_runner import task_runner

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/accounts", response_model=list[AccountOut])
def accounts(platform: Platform | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return list_accounts(db, platform)


@router.post("/accounts", response_model=AccountOut)
def create_account_route(payload: AccountCreate, db: Session = Depends(get_db)) -> dict:
    return create_account(db, payload)


@router.put("/accounts/{account_id}", response_model=AccountOut)
def update_account_route(account_id: int, payload: AccountUpdate, db: Session = Depends(get_db)) -> dict:
    updated = update_account(db, account_id, payload)
    if updated is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return updated


@router.post("/accounts/default", response_model=AccountOut)
def set_default_account_route(payload: AccountSetDefaultRequest, db: Session = Depends(get_db)) -> dict:
    account = set_default_account(db, payload.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.post("/accounts/{account_id}/browser-config/open", response_model=BrowserConfigSessionOut)
def open_account_browser_config(account_id: int, db: Session = Depends(get_db)) -> dict:
    account = get_account(db, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account_browser_service.open_for_account(account)


@router.post("/accounts/{account_id}/browser-config/close", response_model=BrowserConfigSessionOut)
def close_account_browser_config(account_id: int) -> dict:
    return account_browser_service.close_for_account(account_id)


@router.get("/dashboard/overview/{platform}", response_model=OverviewOut)
def dashboard_overview(platform: Platform, db: Session = Depends(get_db)) -> dict:
    return get_overview(db, platform)


@router.get("/dashboard/jobs/{platform}", response_model=PaginatedJobsOut)
def dashboard_jobs(platform: Platform, page: int = 1, page_size: int = 10, db: Session = Depends(get_db)) -> dict:
    return list_jobs(db, platform, page, page_size)


@router.get("/dashboard/deliveries/{platform}", response_model=PaginatedDeliveriesOut)
def dashboard_deliveries(platform: Platform, page: int = 1, page_size: int = 10, db: Session = Depends(get_db)) -> dict:
    return list_deliveries(db, platform, page, page_size)


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
