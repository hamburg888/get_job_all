from datetime import datetime

from pydantic import BaseModel, Field

from app.models.entities import DeliveryStatus, Platform, TaskStatus


class StartTaskRequest(BaseModel):
    platform: Platform
    search_terms: list[str] = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    salary_min: int | None = None
    salary_max: int | None = None
    cooldown_days: int = 7
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


class JobOut(BaseModel):
    id: int
    platform: Platform
    platform_job_id: str
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
    status: DeliveryStatus
    status_reason: str
    chat_sent: bool
    chat_message: str
    created_at: datetime
    job_id: int

    class Config:
        from_attributes = True


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
