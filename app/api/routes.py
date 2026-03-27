import base64
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.models.entities import Platform
from app.schemas.common import (
    AccountCreate,
    AccountOut,
    AccountSetDefaultRequest,
    AccountUpdate,
    AnalysisReportPackOut,
    AnalysisReportPackRequest,
    BrowserConfigSessionOut,
    ConfirmLoginRequest,
    DeliveryOut,
    JobOut,
    LogOut,
    OverviewOut,
    PaginatedDeliveriesOut,
    PaginatedJobsOut,
    PreviewStateOut,
    StartTaskRequest,
    StatsOut,
    StopTaskRequest,
    TaskStatusResponse,
    UploadImageRequest,
    UploadedFileOut,
)
from app.services.account_browser_service import account_browser_service
from app.services.account_service import create_account, get_account, list_accounts, set_default_account, update_account
from app.services.analysis_service import build_analysis_report_pack
from app.services.dashboard_service import DashboardFilters, get_overview, get_stats, list_deliveries, list_jobs, list_logs
from app.services.live_preview_service import live_preview_service
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


@router.post("/uploads/boss-chat-image", response_model=UploadedFileOut)
def upload_boss_chat_image(payload: UploadImageRequest) -> dict:
    suffix = Path(payload.file_name or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
        suffix = ".png"

    target_dir = Path(settings.upload_root).resolve() / "boss_chat_images"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{uuid4().hex}{suffix}"

    try:
        file_bytes = base64.b64decode(payload.content_base64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid image payload") from exc

    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty image payload")

    with target_path.open("wb") as buffer:
        buffer.write(file_bytes)

    return {
        "path": str(target_path),
        "name": payload.file_name or target_path.name,
        "url": f"/uploads/boss_chat_images/{target_path.name}",
    }


def _dashboard_filters(
    account_id: int | None = None,
    statuses: list[str] = Query(default_factory=list),
    send_statuses: list[str] = Query(default_factory=list),
    date_from: str | None = None,
    date_to: str | None = None,
) -> DashboardFilters:
    return DashboardFilters(
        account_id=account_id,
        statuses=tuple(item for item in statuses if item),
        send_statuses=tuple(item for item in send_statuses if item),
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/dashboard/overview/{platform}", response_model=OverviewOut)
def dashboard_overview(
    platform: Platform,
    filters: DashboardFilters = Depends(_dashboard_filters),
    db: Session = Depends(get_db),
) -> dict:
    return get_overview(db, platform, filters)


@router.get("/dashboard/jobs/{platform}", response_model=PaginatedJobsOut)
def dashboard_jobs(
    platform: Platform,
    page: int = 1,
    page_size: int = 10,
    filters: DashboardFilters = Depends(_dashboard_filters),
    db: Session = Depends(get_db),
) -> dict:
    return list_jobs(db, platform, page, page_size, filters)


@router.get("/dashboard/deliveries/{platform}", response_model=PaginatedDeliveriesOut)
def dashboard_deliveries(platform: Platform, page: int = 1, page_size: int = 10, db: Session = Depends(get_db)) -> dict:
    return list_deliveries(db, platform, page, page_size)


@router.get("/dashboard/logs/{platform}", response_model=list[LogOut])
def dashboard_logs(
    platform: Platform,
    limit: int = 40,
    filters: DashboardFilters = Depends(_dashboard_filters),
    db: Session = Depends(get_db),
) -> list:
    return list_logs(db, platform, limit, filters)


@router.get("/dashboard/stats/{platform}", response_model=StatsOut)
def dashboard_stats(
    platform: Platform,
    filters: DashboardFilters = Depends(_dashboard_filters),
    db: Session = Depends(get_db),
) -> dict:
    return get_stats(db, platform, filters)


@router.post("/analysis/report-pack", response_model=AnalysisReportPackOut)
def analysis_report_pack(payload: AnalysisReportPackRequest, db: Session = Depends(get_db)) -> dict:
    return build_analysis_report_pack(db, payload)


@router.get("/tasks/status/{platform}", response_model=TaskStatusResponse)
def get_task_status(platform: Platform) -> TaskStatusResponse:
    return task_runner.status(platform)


@router.get("/tasks/preview/state/{platform}", response_model=PreviewStateOut)
def get_preview_state(platform: Platform) -> dict:
    return live_preview_service.snapshot(platform)


@router.get("/tasks/preview/stream/{platform}")
async def stream_preview(platform: Platform) -> StreamingResponse:
    return StreamingResponse(live_preview_service.stream(platform), media_type="text/event-stream")


@router.post("/tasks/start", response_model=TaskStatusResponse)
def start_task(payload: StartTaskRequest) -> TaskStatusResponse:
    return task_runner.start(payload)


@router.post("/tasks/stop", response_model=TaskStatusResponse)
def stop_task(payload: StopTaskRequest) -> TaskStatusResponse:
    return task_runner.stop(payload.platform)


@router.post("/tasks/confirm-login", response_model=TaskStatusResponse)
def confirm_login(payload: ConfirmLoginRequest) -> TaskStatusResponse:
    return task_runner.confirm_login(payload.platform)
