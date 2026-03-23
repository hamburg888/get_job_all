from datetime import datetime

from pydantic import BaseModel, Field

from app.models.entities import DeliveryStatus, Platform, TaskStatus


class StartTaskRequest(BaseModel):
    platform: Platform
    account_id: int | None = None
    search_terms: list[str] = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    salary_min: int | None = None
    salary_max: int | None = None
    cooldown_days: int = 7
    cross_account_dedupe_enabled: bool = False
    dedupe_match_by_title: bool = True
    dedupe_match_by_company: bool = True
    headless_enabled: bool = False
    live_preview_enabled: bool = False
    live_preview_interval_ms: int = 1000
    auto_send_chat: bool = False
    chat_template: str = "您好，我刚刚投递了贵司的 {job_title} 岗位，期待进一步沟通，谢谢。"


class StopTaskRequest(BaseModel):
    platform: Platform


class ConfirmLoginRequest(BaseModel):
    platform: Platform


class TaskStatusResponse(BaseModel):
    platform: Platform
    status: TaskStatus
    current_step: str
    processed_count: int
    delivered_count: int
    failed_count: int
    started_at: datetime | None = None
    task_run_id: int | None = None
    waiting_for_login: bool = False
    account_id: int | None = None


class JobOut(BaseModel):
    id: int
    platform: Platform
    platform_job_id: str
    account_id: int | None = None
    account_name: str | None = None
    title: str
    company_name: str
    salary_text: str
    salary_min: float | None
    salary_max: float | None
    city: str
    experience_text: str
    detail_url: str
    tags: list[str]
    latest_status: DeliveryStatus
    discovered_at: datetime
    delivered_at: datetime | None = None
    latest_reason: str | None = None


class DeliveryOut(BaseModel):
    id: int
    platform: Platform
    account_id: int | None = None
    account_name: str | None = None
    status: DeliveryStatus
    status_reason: str
    chat_sent: bool
    chat_message: str
    created_at: datetime
    job_id: int

    class Config:
        from_attributes = True


class PaginatedJobsOut(BaseModel):
    items: list[JobOut]
    total: int
    page: int
    page_size: int


class PaginatedDeliveriesOut(BaseModel):
    items: list[DeliveryOut]
    total: int
    page: int
    page_size: int


class LogOut(BaseModel):
    id: int
    platform: Platform
    level: str
    event_type: str
    message: str
    created_at: datetime
    extra_json: dict | None = None

    class Config:
        from_attributes = True


class OverviewOut(BaseModel):
    platform: Platform
    total_jobs: int
    delivered_jobs: int
    failed_jobs: int
    active_task_status: TaskStatus
    last_run_at: datetime | None = None


class StatsBucketOut(BaseModel):
    label: str
    value: int


class StatsOut(BaseModel):
    salary_buckets: list[StatsBucketOut]
    experience_buckets: list[StatsBucketOut]
    city_buckets: list[StatsBucketOut]


class PreviewStateOut(BaseModel):
    platform: Platform
    task_run_id: int | None = None
    active: bool
    enabled: bool
    headless_enabled: bool
    interval_ms: int
    seq: int
    frame_url: str | None = None
    updated_at: datetime | None = None
    current_step: str
    message: str


class AccountBase(BaseModel):
    platform: Platform
    phone: str = ""
    username: str = ""
    remark: str = ""
    is_default: bool = False
    is_active: bool = True


class AccountCreate(AccountBase):
    pass


class AccountUpdate(AccountBase):
    pass


class AccountOut(AccountBase):
    id: int
    profile_dir: str
    today_delivered_count: int = 0
    total_delivered_count: int = 0


class AccountSetDefaultRequest(BaseModel):
    account_id: int


class BrowserConfigSessionOut(BaseModel):
    account_id: int
    active: bool
    message: str
