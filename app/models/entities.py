from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time_utils import local_now
from app.db.database import Base


class Platform(StrEnum):
    BOSS = "boss"
    LIEPIN = "liepin"
    QIANCHENG = "qiancheng"
    ZHILIAN = "zhilian"


class TaskStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    SKIPPED = "skipped"
    FAILED = "failed"


class ChatSendStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class ResumeSyncStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class RuleConfig(Base):
    __tablename__ = "rule_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    keyword: Mapped[str] = mapped_column(String(120))
    city: Mapped[str] = mapped_column(String(60), default="")
    enable_company_blacklist: Mapped[bool] = mapped_column(Boolean, default=False)
    company_blacklist: Mapped[str] = mapped_column(Text, default="")
    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cooldown_days: Mapped[int] = mapped_column(Integer, default=7)
    cross_account_dedupe_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    dedupe_match_by_title: Mapped[bool] = mapped_column(Boolean, default=True)
    dedupe_match_by_company: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_send_chat: Mapped[bool] = mapped_column(Boolean, default=False)
    chat_template: Mapped[str] = mapped_column(
        Text,
        default="您好，我刚刚投递了贵司的 {job_title} 岗位，期待进一步沟通，谢谢。",
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now)


class TaskRun(Base):
    __tablename__ = "task_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    keyword: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default=TaskStatus.IDLE.value)
    current_step: Mapped[str] = mapped_column(String(200), default="等待启动")
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    delivered_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)
    current_step_started_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    account: Mapped["Account | None"] = relationship(back_populates="task_runs")
    jobs: Mapped[list["Job"]] = relationship(back_populates="task_run")
    deliveries: Mapped[list["DeliveryRecord"]] = relationship(back_populates="task_run")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    phone: Mapped[str] = mapped_column(String(30), default="")
    username: Mapped[str] = mapped_column(String(120), default="")
    remark: Mapped[str] = mapped_column(String(255), default="")
    profile_dir: Mapped[str] = mapped_column(String(255), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now)

    task_runs: Mapped[list["TaskRun"]] = relationship(back_populates="account")
    deliveries: Mapped[list["DeliveryRecord"]] = relationship(back_populates="account")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    platform_job_id: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(200))
    company_name: Mapped[str] = mapped_column(String(200))
    salary_text: Mapped[str] = mapped_column(String(80))
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    city: Mapped[str] = mapped_column(String(80), default="")
    experience_text: Mapped[str] = mapped_column(String(80), default="")
    education_text: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    detail_url: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    latest_status: Mapped[str] = mapped_column(String(20), default=DeliveryStatus.PENDING.value)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)
    task_run_id: Mapped[int | None] = mapped_column(ForeignKey("task_runs.id"), nullable=True)

    task_run: Mapped["TaskRun | None"] = relationship(back_populates="jobs")
    deliveries: Mapped[list["DeliveryRecord"]] = relationship(back_populates="job")


class DeliveryRecord(Base):
    __tablename__ = "delivery_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    task_run_id: Mapped[int] = mapped_column(ForeignKey("task_runs.id"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), default=DeliveryStatus.PENDING.value)
    status_reason: Mapped[str] = mapped_column(String(255), default="")
    send_status: Mapped[str] = mapped_column(String(20), default=ChatSendStatus.SKIPPED.value)
    chat_hr_name: Mapped[str] = mapped_column(String(120), default="")
    chat_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    chat_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)

    job: Mapped[Job] = relationship(back_populates="deliveries")
    task_run: Mapped[TaskRun] = relationship(back_populates="deliveries")
    account: Mapped["Account | None"] = relationship(back_populates="deliveries")


class TaskLog(Base):
    __tablename__ = "task_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    task_run_id: Mapped[int | None] = mapped_column(ForeignKey("task_runs.id"), nullable=True)
    level: Mapped[str] = mapped_column(String(20), default="INFO")
    event_type: Mapped[str] = mapped_column(String(40), default="info")
    message: Mapped[str] = mapped_column(Text)
    extra_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)


class MasterResume(Base):
    __tablename__ = "master_resumes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_platform: Mapped[str] = mapped_column(String(20), default="")
    source_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    source_label: Mapped[str] = mapped_column(String(120), default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    full_name: Mapped[str] = mapped_column(String(120), default="")
    headline: Mapped[str] = mapped_column(String(255), default="")
    contact_summary: Mapped[str] = mapped_column(String(255), default="")
    wechat_id: Mapped[str] = mapped_column(String(120), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    job_preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    work_experiences: Mapped[list[dict]] = mapped_column(JSON, default=list)
    project_experiences: Mapped[list[dict]] = mapped_column(JSON, default=list)
    education_experiences: Mapped[list[dict]] = mapped_column(JSON, default=list)
    certificates: Mapped[list[str]] = mapped_column(JSON, default=list)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    languages: Mapped[list[str]] = mapped_column(JSON, default=list)
    attachments: Mapped[list[dict]] = mapped_column(JSON, default=list)
    raw_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now)

    source_account: Mapped["Account | None"] = relationship()


class ResumeImportLog(Base):
    __tablename__ = "resume_import_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_platform: Mapped[str] = mapped_column(String(20), index=True)
    source_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=ResumeSyncStatus.PENDING.value)
    message: Mapped[str] = mapped_column(Text, default="")
    extra_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    source_account: Mapped["Account | None"] = relationship()


class ResumeSyncRun(Base):
    __tablename__ = "resume_sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    master_resume_id: Mapped[int | None] = mapped_column(ForeignKey("master_resumes.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=ResumeSyncStatus.PENDING.value)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    master_resume: Mapped["MasterResume | None"] = relationship()
    targets: Mapped[list["ResumeSyncTarget"]] = relationship(back_populates="sync_run")


class ResumeSyncTarget(Base):
    __tablename__ = "resume_sync_targets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("resume_sync_runs.id"))
    target_platform: Mapped[str] = mapped_column(String(20), index=True)
    target_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=ResumeSyncStatus.PENDING.value)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    sync_run: Mapped["ResumeSyncRun"] = relationship(back_populates="targets")
    target_account: Mapped["Account | None"] = relationship()
