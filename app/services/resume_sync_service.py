from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.automation.adapters.liepin_adapter import LiepinAdapter
from app.automation.adapters.boss_adapter import BossAdapter
from app.automation.adapters.qiancheng_adapter import QianchengAdapter
from app.automation.adapters.zhilian_adapter import ZhilianAdapter
from app.core.time_utils import local_now
from app.models.entities import (
    Account,
    MasterResume,
    Platform,
    ResumeImportLog,
    ResumeSyncRun,
    ResumeSyncStatus,
    ResumeSyncTarget,
)
from app.services.profile_path_resolver import resolve_profile_dir


def _safe_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return f"{exc.__class__.__name__}: {exc!r}"


def _platform_support() -> dict[str, dict[str, bool]]:
    return {
        Platform.LIEPIN.value: {"import": True, "sync": True},
        Platform.BOSS.value: {"import": True, "sync": True},
        Platform.ZHILIAN.value: {"import": True, "sync": True},
        Platform.QIANCHENG.value: {"import": True, "sync": True},
    }


def _resolve_account(db: Session, platform: Platform, account_id: int | None) -> Account | None:
    if account_id is not None:
        account = db.get(Account, account_id)
        if account and account.platform == platform.value:
            return account
        return None
    return db.scalar(
        select(Account)
        .where(Account.platform == platform.value, Account.is_active.is_(True))
        .order_by(Account.is_default.desc(), Account.id.asc())
        .limit(1)
    )


def _serialize_master_resume(resume: MasterResume | None) -> dict | None:
    if resume is None:
        return None
    return {
        "id": resume.id,
        "source_platform": resume.source_platform,
        "source_account_id": resume.source_account_id,
        "source_label": resume.source_label,
        "source_url": resume.source_url,
        "full_name": resume.full_name,
        "headline": resume.headline,
        "contact_summary": resume.contact_summary,
        "wechat_id": resume.wechat_id,
        "summary": resume.summary,
        "job_preferences": resume.job_preferences or {},
        "work_experiences": resume.work_experiences or [],
        "project_experiences": resume.project_experiences or [],
        "education_experiences": resume.education_experiences or [],
        "certificates": resume.certificates or [],
        "skills": resume.skills or [],
        "languages": resume.languages or [],
        "attachments": resume.attachments or [],
        "raw_snapshot": resume.raw_snapshot or {},
        "imported_at": resume.imported_at,
        "updated_at": resume.updated_at,
    }


def _filter_master_resume_for_sync(master_resume: dict, field_selection: dict[str, bool] | None) -> dict:
    selection = {str(key): bool(value) for key, value in (field_selection or {}).items()}
    if not selection:
        return dict(master_resume)

    filtered = dict(master_resume)
    raw_snapshot = dict(filtered.get("raw_snapshot") or {})

    if not selection.get("basic_info", False):
        filtered["full_name"] = ""
        filtered["headline"] = ""
        filtered["contact_summary"] = ""
        filtered["wechat_id"] = ""
        manual_profile = dict(raw_snapshot.get("manual_profile") or {})
        for key in (
            "experience_text",
            "age_text",
            "degree_text",
            "job_status_text",
            "arrival_text",
            "contact_name",
            "phone",
            "email",
            "wechat",
        ):
            manual_profile[key] = ""
        raw_snapshot["manual_profile"] = manual_profile

    if not selection.get("summary", True):
        filtered["summary"] = ""
    if not selection.get("job_preferences", True):
        filtered["job_preferences"] = {}
    if not selection.get("work_experiences", True):
        filtered["work_experiences"] = []
    if not selection.get("project_experiences", True):
        filtered["project_experiences"] = []
    if not selection.get("education_experiences", True):
        filtered["education_experiences"] = []
    if not selection.get("certificates", True):
        filtered["certificates"] = []
    if not selection.get("skills", True):
        filtered["skills"] = []
    if not selection.get("languages", True):
        filtered["languages"] = []
    if not selection.get("attachments", True):
        filtered["attachments"] = []

    filtered["raw_snapshot"] = raw_snapshot
    return filtered


def _serialize_import_log(log: ResumeImportLog) -> dict:
    return {
        "id": log.id,
        "source_platform": log.source_platform,
        "source_account_id": log.source_account_id,
        "status": log.status,
        "message": log.message,
        "extra_json": log.extra_json,
        "created_at": log.created_at,
        "finished_at": log.finished_at,
    }


def _serialize_sync_run(run: ResumeSyncRun) -> dict:
    return {
        "id": run.id,
        "master_resume_id": run.master_resume_id,
        "status": run.status,
        "message": run.message,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "targets": [
            {
                "id": target.id,
                "target_platform": target.target_platform,
                "target_account_id": target.target_account_id,
                "status": target.status,
                "message": target.message,
                "created_at": target.created_at,
                "finished_at": target.finished_at,
            }
            for target in sorted(run.targets, key=lambda item: item.id)
        ],
    }


def get_resume_center(db: Session) -> dict:
    resume = db.scalar(select(MasterResume).order_by(desc(MasterResume.updated_at)).limit(1))
    import_logs = db.scalars(select(ResumeImportLog).order_by(desc(ResumeImportLog.created_at)).limit(12)).all()
    sync_runs = db.scalars(select(ResumeSyncRun).order_by(desc(ResumeSyncRun.created_at)).limit(12)).all()
    return {
        "master_resume": _serialize_master_resume(resume),
        "import_logs": [_serialize_import_log(log) for log in import_logs],
        "sync_runs": [_serialize_sync_run(run) for run in sync_runs],
        "platform_support": _platform_support(),
    }


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_string_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        normalized = _normalize_text(item)
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(normalized)
    return result


def _normalize_dict_list(values: object) -> list[dict]:
    if not isinstance(values, list):
        return []
    result: list[dict] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        normalized_item: dict[str, object] = {}
        for key, value in item.items():
            if not isinstance(key, str):
                continue
            if isinstance(value, str):
                normalized_item[key] = value.strip()
            elif isinstance(value, list):
                normalized_item[key] = [
                    entry.strip() if isinstance(entry, str) else entry
                    for entry in value
                    if not isinstance(entry, str) or entry.strip()
                ]
            else:
                normalized_item[key] = value
        if any(bool(value) for value in normalized_item.values()):
            result.append(normalized_item)
    return result


def update_master_resume(db: Session, payload: dict) -> dict:
    resume = db.scalar(select(MasterResume).order_by(desc(MasterResume.updated_at)).limit(1))
    if resume is None:
        resume = MasterResume(
            source_platform="manual",
            source_label="本站编辑",
            source_url="",
        )
        db.add(resume)

    resume.full_name = _normalize_text(payload.get("full_name"))
    resume.headline = _normalize_text(payload.get("headline"))
    resume.contact_summary = _normalize_text(payload.get("contact_summary"))
    resume.wechat_id = _normalize_text(payload.get("wechat_id"))
    resume.summary = _normalize_text(payload.get("summary"))
    resume.job_preferences = dict(payload.get("job_preferences") or {})
    resume.work_experiences = _normalize_dict_list(payload.get("work_experiences"))
    resume.project_experiences = _normalize_dict_list(payload.get("project_experiences"))
    resume.education_experiences = _normalize_dict_list(payload.get("education_experiences"))
    resume.certificates = _normalize_string_list(payload.get("certificates"))
    resume.skills = _normalize_string_list(payload.get("skills"))
    resume.languages = _normalize_string_list(payload.get("languages"))
    resume.attachments = _normalize_dict_list(payload.get("attachments"))
    if isinstance(payload.get("raw_snapshot"), dict):
        resume.raw_snapshot = dict(payload.get("raw_snapshot") or {})
    resume.source_platform = resume.source_platform or "manual"
    resume.source_label = "本站编辑"
    resume.updated_at = local_now()

    db.commit()
    return get_resume_center(db)


def import_resume_from_platform(
    db: Session,
    source_platform: Platform,
    account_id: int | None = None,
    *,
    headless_enabled: bool = False,
) -> dict:
    account = _resolve_account(db, source_platform, account_id)
    if account is None:
        raise RuntimeError("未找到可用账号，请先为该平台创建并配置账号。")

    log = ResumeImportLog(
        source_platform=source_platform.value,
        source_account_id=account.id,
        status=ResumeSyncStatus.RUNNING.value,
        message="正在读取平台简历数据",
        extra_json={"headless_enabled": headless_enabled},
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    try:
        if source_platform == Platform.LIEPIN:
            adapter = LiepinAdapter()
        elif source_platform == Platform.BOSS:
            adapter = BossAdapter()
        elif source_platform == Platform.ZHILIAN:
            adapter = ZhilianAdapter()
        elif source_platform == Platform.QIANCHENG:
            adapter = QianchengAdapter()
        else:
            raise RuntimeError("暂不支持该平台的简历导入。")
        snapshot = adapter.import_resume_snapshot(resolve_profile_dir(account.profile_dir), headless=headless_enabled)

        resume = db.scalar(select(MasterResume).order_by(desc(MasterResume.updated_at)).limit(1))
        if resume is None:
            resume = MasterResume()
            db.add(resume)

        resume.source_platform = source_platform.value
        resume.source_account_id = account.id
        resume.source_label = account.username or account.phone or f"{source_platform.value}账号"
        resume.source_url = str(snapshot.get("source_url", ""))
        resume.full_name = str(snapshot.get("full_name", ""))
        resume.headline = str(snapshot.get("headline", ""))
        resume.contact_summary = str(snapshot.get("contact_summary", ""))
        resume.wechat_id = str(snapshot.get("wechat_id", ""))
        resume.summary = str(snapshot.get("summary", ""))
        resume.job_preferences = dict(snapshot.get("job_preferences", {}) or {})
        resume.work_experiences = list(snapshot.get("work_experiences", []) or [])
        resume.project_experiences = list(snapshot.get("project_experiences", []) or [])
        resume.education_experiences = list(snapshot.get("education_experiences", []) or [])
        resume.certificates = list(snapshot.get("certificates", []) or [])
        resume.skills = list(snapshot.get("skills", []) or [])
        resume.languages = list(snapshot.get("languages", []) or [])
        resume.attachments = list(snapshot.get("attachments", []) or [])
        resume.raw_snapshot = dict(snapshot.get("raw_snapshot", {}) or {})
        resume.imported_at = local_now()

        log.status = ResumeSyncStatus.COMPLETED.value
        log.message = "导入成功，已更新本站主简历"
        log.extra_json = {
            "summary_length": len(resume.summary or ""),
            "work_experiences": len(resume.work_experiences or []),
            "project_experiences": len(resume.project_experiences or []),
            "education_experiences": len(resume.education_experiences or []),
            "skills": len(resume.skills or []),
        }
        log.finished_at = local_now()
        db.commit()
    except Exception as exc:
        log.status = ResumeSyncStatus.FAILED.value
        log.message = _safe_error_message(exc)
        log.finished_at = local_now()
        db.commit()
        raise

    return get_resume_center(db)


def sync_master_resume_to_platforms(
    db: Session,
    target_platforms: list[Platform],
    account_map: dict[str, int | None] | None = None,
    field_selection: dict[str, bool] | None = None,
) -> dict:
    resume = db.scalar(select(MasterResume).order_by(desc(MasterResume.updated_at)).limit(1))
    if resume is None:
        raise RuntimeError("当前还没有主简历，请先从平台导入一份简历。")

    requested_platforms = list(dict.fromkeys(target_platforms))
    if not requested_platforms:
        raise RuntimeError("请至少选择一个目标平台。")
    filtered_resume = _filter_master_resume_for_sync(_serialize_master_resume(resume) or {}, field_selection)

    run = ResumeSyncRun(
        master_resume_id=resume.id,
        status=ResumeSyncStatus.RUNNING.value,
        message="已创建同步任务，正在按顺序处理目标平台",
    )
    db.add(run)
    db.flush()

    support = _platform_support()
    targets: list[ResumeSyncTarget] = []
    for platform in requested_platforms:
        requested_account_id = (account_map or {}).get(platform.value)
        account = _resolve_account(db, platform, requested_account_id)
        target = ResumeSyncTarget(
            sync_run_id=run.id,
            target_platform=platform.value,
            target_account_id=account.id if account else None,
            status=ResumeSyncStatus.PENDING.value,
            message="等待处理",
        )
        db.add(target)
        targets.append(target)
    db.commit()

    overall_failed = False
    for target in targets:
        target.status = ResumeSyncStatus.RUNNING.value
        target.message = "正在准备同步"
        db.commit()

        if not support.get(target.target_platform, {}).get("sync", False):
            target.status = ResumeSyncStatus.SKIPPED.value
            target.message = "该平台写回同步适配器尚未接入，当前先保留目标选择与日志能力。"
            target.finished_at = local_now()
            overall_failed = True
            db.commit()
            continue

        account = db.get(Account, target.target_account_id) if target.target_account_id else None
        if account is None:
            target.status = ResumeSyncStatus.FAILED.value
            target.message = "未找到可用目标账号，请先为该平台创建并配置账号。"
            target.finished_at = local_now()
            overall_failed = True
            db.commit()
            continue

        try:
            if target.target_platform == Platform.LIEPIN.value:
                adapter = LiepinAdapter()
                sync_result = adapter.sync_master_resume(
                    resolve_profile_dir(account.profile_dir),
                    filtered_resume,
                )
                target.status = ResumeSyncStatus.COMPLETED.value
                target.message = str(sync_result.get("message") or "猎聘同步完成")
            elif target.target_platform == Platform.QIANCHENG.value:
                adapter = QianchengAdapter()
                sync_result = adapter.sync_master_resume(
                    resolve_profile_dir(account.profile_dir),
                    filtered_resume,
                )
                target.status = ResumeSyncStatus.COMPLETED.value
                target.message = str(sync_result.get("message") or "前程无忧同步完成")
            elif target.target_platform == Platform.BOSS.value:
                adapter = BossAdapter()
                sync_result = adapter.sync_master_resume(
                    resolve_profile_dir(account.profile_dir),
                    filtered_resume,
                )
                target.status = ResumeSyncStatus.COMPLETED.value
                target.message = str(sync_result.get("message") or "BOSS 同步完成")
            elif target.target_platform == Platform.ZHILIAN.value:
                adapter = ZhilianAdapter()
                sync_result = adapter.sync_master_resume(
                    resolve_profile_dir(account.profile_dir),
                    filtered_resume,
                )
                target.status = ResumeSyncStatus.COMPLETED.value
                target.message = str(sync_result.get("message") or "智联同步完成")
            else:
                target.status = ResumeSyncStatus.SKIPPED.value
                target.message = "该平台写回同步适配器尚未接入，当前先保留目标选择与日志能力。"
                overall_failed = True
            target.finished_at = local_now()
            db.commit()
        except Exception as exc:
            target.status = ResumeSyncStatus.FAILED.value
            target.message = _safe_error_message(exc)
            target.finished_at = local_now()
            overall_failed = True
            db.commit()

    run.status = ResumeSyncStatus.FAILED.value if overall_failed else ResumeSyncStatus.COMPLETED.value
    run.message = (
        "同步流程已结束，存在失败或未完成的平台，请查看目标日志。"
        if overall_failed
        else "所有目标平台已完成同步。"
    )
    run.finished_at = local_now()
    db.commit()

    return get_resume_center(db)
