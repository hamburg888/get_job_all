from datetime import datetime

from pydantic import BaseModel, Field

from app.models.entities import DeliveryStatus, Platform, TaskStatus


def _default_boss_reply_rules() -> list[dict]:
    return [
        {
            "id": "resume-material",
            "label": "简历资料",
            "enabled": True,
            "keywords": ["简历", "发简历", "发送简历", "发份简历"],
            "reply_template": "好的，关于 {job_title} 的简历资料我可以继续补充给您。",
        },
        {
            "id": "wechat-contact",
            "label": "微信沟通",
            "enabled": True,
            "keywords": ["微信", "加微信", "互换微信", "交换微信", "vx", "v我"],
            "reply_template": "可以的，关于 {job_title} 的详细情况我们可以继续沟通，您也可以先把联系方式发我。",
        },
        {
            "id": "salary-interview",
            "label": "薪资面试",
            "enabled": True,
            "keywords": ["薪资", "待遇", "面试", "到岗", "入职"],
            "reply_template": "收到，关于 {job_title} 的薪资和面试安排，我这边愿意继续配合沟通。",
        },
    ]


class BossReplyRuleIn(BaseModel):
    id: str = ""
    label: str = ""
    enabled: bool = True
    keywords: list[str] = Field(default_factory=list)
    reply_template: str = ""


class StartTaskRequest(BaseModel):
    platform: Platform
    account_id: int | None = None
    search_terms: list[str] = Field(default_factory=list)
    use_manual_entry_url: bool = False
    manual_entry_url: str = ""
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
    delivery_interval_seconds: int = 3
    max_deliveries_per_run: int = 100
    auto_send_chat: bool = False
    use_chat_template: bool = False
    chat_template: str = "您好，我刚刚投递了贵司的 {job_title} 岗位，期待进一步沟通，谢谢。"
    enable_common_phrases: bool = True
    common_phrase_indexes: list[int] = Field(default_factory=lambda: [1])
    enable_image_resume: bool = False
    image_resume_path: str = ""
    auto_agree_wechat_card: bool = False
    enable_follow_up_reply: bool = True
    boss_reply_rules: list[BossReplyRuleIn] = Field(default_factory=_default_boss_reply_rules)
    boss_active_statuses: list[str] = Field(default_factory=lambda: ["不限"])


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
    current_step_started_at: datetime | None = None
    finished_at: datetime | None = None
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
    description: str
    detail_url: str
    tags: list[str]
    latest_status: DeliveryStatus
    discovered_at: datetime
    delivered_at: datetime | None = None
    latest_reason: str | None = None
    latest_send_status: str | None = None
    latest_chat_message: str | None = None


class DeliveryOut(BaseModel):
    id: int
    platform: Platform
    account_id: int | None = None
    account_name: str | None = None
    status: DeliveryStatus
    status_reason: str
    send_status: str
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


class UploadedFileOut(BaseModel):
    path: str
    name: str
    url: str | None = None


class UploadImageRequest(BaseModel):
    file_name: str
    content_base64: str
