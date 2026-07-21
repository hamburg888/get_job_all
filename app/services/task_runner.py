import contextlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
import sys

from sqlalchemy import desc, select

from app.automation.adapters.boss_adapter import BossAdapter
from app.automation.adapters.liepin_adapter import LiepinAdapter
from app.automation.adapters.qiancheng_adapter import QianchengAdapter
from app.automation.adapters.yupao_adapter import YupaoAdapter
from app.automation.adapters.zhilian_adapter import ZhilianAdapter, ZhilianDailyLimitReached
from app.core.config import settings
from app.core.time_utils import local_now
from app.db.database import SessionLocal
from app.models.entities import (
    Account,
    ChatSendStatus,
    DeliveryRecord,
    DeliveryStatus,
    Job,
    Platform,
    RuleConfig,
    TaskLog,
    TaskRun,
    TaskStatus,
)
from app.schemas.common import StartTaskRequest, TaskStatusResponse
from app.services.live_preview_service import live_preview_service
from app.services.account_browser_service import account_browser_service
from app.services.account_service import get_account

logger = logging.getLogger(__name__)


@dataclass
class WorkerState:
    thread: threading.Thread
    stop_event: threading.Event
    login_confirm_event: threading.Event
    task_run_id: int
    force_stop: Callable[[], None] | None = None


class TaskRunner:
    def __init__(self) -> None:
        self._workers: dict[str, WorkerState] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _task_keyword(payload: StartTaskRequest) -> str:
        if payload.use_manual_entry_url:
            return payload.manual_entry_url
        if payload.search_terms:
            return ",".join(payload.search_terms)
        if payload.platform == Platform.QIANCHENG:
            return "默认搜索页"
        return ""

    @staticmethod
    def _platform_supports_chat(platform: Platform) -> bool:
        return platform in {Platform.BOSS, Platform.LIEPIN, Platform.YUPAO}

    @staticmethod
    def _serialize_task_config(payload: StartTaskRequest) -> dict:
        auto_send_chat = TaskRunner._derive_auto_send_chat(payload)
        return {
            "search_terms": list(payload.search_terms or []),
            "use_manual_entry_url": payload.use_manual_entry_url,
            "manual_entry_url": payload.manual_entry_url or "",
            "keywords": list(payload.keywords or []),
            "cities": list(payload.cities or []),
            "enable_company_blacklist": payload.enable_company_blacklist,
            "company_blacklist": list(payload.company_blacklist or []),
            "enable_job_title_blacklist": payload.enable_job_title_blacklist,
            "job_title_blacklist": list(payload.job_title_blacklist or []),
            "salary_min": payload.salary_min,
            "salary_max": payload.salary_max,
            "cooldown_days": payload.cooldown_days,
            "cross_account_dedupe_enabled": payload.cross_account_dedupe_enabled,
            "dedupe_match_by_title": payload.dedupe_match_by_title,
            "dedupe_match_by_company": payload.dedupe_match_by_company,
            "headless_enabled": payload.headless_enabled,
            "live_preview_enabled": payload.live_preview_enabled,
            "delivery_interval_seconds": payload.delivery_interval_seconds,
            "max_deliveries_per_run": payload.max_deliveries_per_run,
            "auto_send_chat": auto_send_chat,
            "use_chat_template": payload.use_chat_template,
            "chat_template": payload.chat_template or "",
            "enable_common_phrases": payload.enable_common_phrases,
            "common_phrase_indexes": list(payload.common_phrase_indexes or []),
            "enable_image_resume": payload.enable_image_resume,
            "image_resume_path": payload.image_resume_path or "",
            "auto_agree_wechat_card": payload.auto_agree_wechat_card,
            "boss_first_greeting_only": payload.boss_first_greeting_only,
            "enable_follow_up_reply": payload.enable_follow_up_reply,
            "boss_active_statuses": list(payload.boss_active_statuses or []),
            "remark": payload.remark or "",
            "account_id": payload.account_id,
        }

    @staticmethod
    def _normalize_text_filters(values: list[str]) -> list[str]:
        normalized_items: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = " ".join(str(value or "").strip().split())
            if not item:
                continue
            item_key = item.lower()
            if item_key in seen:
                continue
            seen.add(item_key)
            normalized_items.append(item)
        return normalized_items

    @staticmethod
    def _ensure_account_profile_available(account: Account) -> None:
        if account_browser_service.is_profile_occupied(account.profile_dir):
            raise RuntimeError("该账号浏览器配置窗口正在占用同一浏览器配置目录，请先关闭浏览器配置后再启动任务。")

    def start(self, payload: StartTaskRequest) -> TaskStatusResponse:
        platform_key = payload.platform.value
        with self._lock:
            existing = self._workers.get(platform_key)
            if existing and existing.thread.is_alive():
                return self.status(payload.platform)

            with SessionLocal() as db:
                account = self._resolve_account(db, payload)
                self._ensure_account_profile_available(account)
                supports_chat = self._platform_supports_chat(payload.platform)
                auto_send_chat = self._derive_auto_send_chat(payload)
                payload.auto_send_chat = auto_send_chat
                chat_template = payload.chat_template if supports_chat else ""
                normalized_company_blacklist = self._normalize_text_filters(payload.company_blacklist)
                payload.company_blacklist = normalized_company_blacklist
                rule = db.scalar(select(RuleConfig).where(RuleConfig.platform == platform_key).limit(1))
                if rule:
                    rule.keyword = ",".join(payload.keywords or payload.search_terms)
                    rule.city = ",".join(payload.cities)
                    rule.enable_company_blacklist = payload.enable_company_blacklist
                    rule.company_blacklist = json.dumps(normalized_company_blacklist, ensure_ascii=False)
                    rule.salary_min = payload.salary_min
                    rule.salary_max = payload.salary_max
                    rule.cooldown_days = payload.cooldown_days
                    rule.cross_account_dedupe_enabled = payload.cross_account_dedupe_enabled
                    rule.dedupe_match_by_title = payload.dedupe_match_by_title
                    rule.dedupe_match_by_company = payload.dedupe_match_by_company
                    rule.auto_send_chat = auto_send_chat
                    rule.chat_template = chat_template
                else:
                    rule = RuleConfig(
                        platform=platform_key,
                        keyword=",".join(payload.keywords or payload.search_terms),
                        city=",".join(payload.cities),
                        enable_company_blacklist=payload.enable_company_blacklist,
                        company_blacklist=json.dumps(normalized_company_blacklist, ensure_ascii=False),
                        salary_min=payload.salary_min,
                        salary_max=payload.salary_max,
                        cooldown_days=payload.cooldown_days,
                        cross_account_dedupe_enabled=payload.cross_account_dedupe_enabled,
                        dedupe_match_by_title=payload.dedupe_match_by_title,
                        dedupe_match_by_company=payload.dedupe_match_by_company,
                        auto_send_chat=auto_send_chat,
                        chat_template=chat_template,
                    )
                    db.add(rule)

                task = TaskRun(
                    account_id=account.id if account else None,
                    platform=platform_key,
                    keyword=self._task_keyword(payload),
                    status=TaskStatus.RUNNING.value,
                    current_step="正在准备浏览器与筛选条件",
                    current_step_started_at=local_now(),
                    processed_count=0,
                    delivered_count=0,
                    failed_count=0,
                    config_json=self._serialize_task_config(payload),
                )
                db.add(task)
                db.flush()
                task_id = task.id
                db.add(
                    TaskLog(
                        platform=platform_key,
                        task_run_id=task_id,
                        event_type="task_start",
                        message=f"任务已启动，账号: {account.username if account else '未指定'}",
                    )
                )
                db.add(
                    TaskLog(
                        platform=platform_key,
                        task_run_id=task_id,
                        event_type="task_config",
                        message=(
                            "启动参数："
                            f"headless_enabled={payload.headless_enabled}, "
                            f"live_preview_enabled={payload.live_preview_enabled}, "
                            f"use_manual_entry_url={payload.use_manual_entry_url}, "
                            f"entry_url={payload.manual_entry_url or '-'}, "
                            f"search_terms={','.join(payload.search_terms) or '-'}, "
                            f"enable_company_blacklist={payload.enable_company_blacklist}, "
                            f"company_blacklist_count={len(normalized_company_blacklist)}"
                        ),
                        extra_json={
                            "account_id": payload.account_id,
                            "headless_enabled": payload.headless_enabled,
                            "live_preview_enabled": payload.live_preview_enabled,
                            "use_manual_entry_url": payload.use_manual_entry_url,
                            "manual_entry_url": payload.manual_entry_url,
                            "search_terms": payload.search_terms,
                            "enable_company_blacklist": payload.enable_company_blacklist,
                            "company_blacklist": normalized_company_blacklist,
                        },
                    )
                )
                db.commit()
                logger.info(
                    "Task start payload resolved: platform=%s account_id=%s headless_enabled=%s live_preview_enabled=%s use_manual_entry_url=%s entry_url=%s",
                    platform_key,
                    payload.account_id,
                    payload.headless_enabled,
                    payload.live_preview_enabled,
                    payload.use_manual_entry_url,
                    payload.manual_entry_url,
                )
                live_preview_service.start_session(
                    payload.platform,
                    task_id,
                    headless_enabled=payload.headless_enabled,
                    live_preview_enabled=payload.live_preview_enabled,
                    interval_ms=payload.live_preview_interval_ms,
                    current_step=task.current_step,
                )

            stop_event = threading.Event()
            login_confirm_event = threading.Event()
            thread = threading.Thread(target=self._run_task, args=(task_id, payload, stop_event), daemon=True)
            self._workers[platform_key] = WorkerState(
                thread=thread,
                stop_event=stop_event,
                login_confirm_event=login_confirm_event,
                task_run_id=task_id,
            )
            thread.start()

        return self.status(payload.platform)

    def stop(self, platform: Platform) -> TaskStatusResponse:
        with self._lock:
            worker = self._workers.get(platform.value)
            if worker:
                worker.stop_event.set()
                with contextlib.suppress(Exception):
                    if worker.force_stop is not None:
                        worker.force_stop()
                self._set_status(worker.task_run_id, TaskStatus.STOPPING, "收到停止指令，正在收尾")
                with SessionLocal() as db:
                    task = db.get(TaskRun, worker.task_run_id)
                    return TaskStatusResponse(
                        platform=platform,
                        status=TaskStatus.STOPPING,
                        current_step="收到停止指令，正在收尾",
                    processed_count=task.processed_count if task else 0,
                    delivered_count=task.delivered_count if task else 0,
                    failed_count=task.failed_count if task else 0,
                    started_at=task.started_at if task else None,
                    current_step_started_at=task.current_step_started_at if task else None,
                    finished_at=task.finished_at if task else None,
                    task_run_id=worker.task_run_id,
                    waiting_for_login=False,
                    account_id=task.account_id if task else None,
                )
        with SessionLocal() as db:
            task = db.scalar(
                select(TaskRun).where(TaskRun.platform == platform.value).order_by(desc(TaskRun.started_at)).limit(1)
            )
            if task and task.status in {TaskStatus.RUNNING.value, TaskStatus.STOPPING.value}:
                task.status = TaskStatus.STOPPED.value
                self._assign_current_step(task, "任务已停止")
                task.finished_at = local_now()
                db.commit()
                return TaskStatusResponse(
                    platform=platform,
                    status=TaskStatus.STOPPED,
                    current_step="任务已停止",
                    processed_count=task.processed_count,
                    delivered_count=task.delivered_count,
                    failed_count=task.failed_count,
                    started_at=task.started_at,
                    current_step_started_at=task.current_step_started_at,
                    finished_at=task.finished_at,
                    task_run_id=task.id,
                    waiting_for_login=False,
                    account_id=task.account_id,
                )
        return TaskStatusResponse(
            platform=platform,
            status=TaskStatus.STOPPED,
            current_step="当前没有正在运行的任务",
            processed_count=0,
            delivered_count=0,
            failed_count=0,
            waiting_for_login=False,
        )

    def status(self, platform: Platform) -> TaskStatusResponse:
        with self._lock:
            worker = self._workers.get(platform.value)
            worker_alive = bool(worker and worker.thread.is_alive())

        with SessionLocal() as db:
            task = db.scalar(
                select(TaskRun).where(TaskRun.platform == platform.value).order_by(desc(TaskRun.started_at)).limit(1)
            )
            if not task:
                return TaskStatusResponse(
                    platform=platform,
                    status=TaskStatus.IDLE,
                    current_step="暂无任务",
                    processed_count=0,
                    delivered_count=0,
                    failed_count=0,
                )
            if not worker_alive and task.status in {TaskStatus.RUNNING.value, TaskStatus.STOPPING.value}:
                task.status = TaskStatus.STOPPED.value
                self._assign_current_step(task, "任务已停止")
                task.finished_at = local_now()
                db.commit()
            waiting_for_login = "登录" in task.current_step and "确认" in task.current_step
            return TaskStatusResponse(
                platform=platform,
                status=TaskStatus(task.status),
                current_step=task.current_step,
                processed_count=task.processed_count,
                delivered_count=task.delivered_count,
                failed_count=task.failed_count,
                started_at=task.started_at,
                current_step_started_at=task.current_step_started_at,
                finished_at=task.finished_at,
                task_run_id=task.id,
                waiting_for_login=waiting_for_login,
                account_id=task.account_id,
            )

    def confirm_login(self, platform: Platform) -> TaskStatusResponse:
        with self._lock:
            worker = self._workers.get(platform.value)
            if worker:
                worker.login_confirm_event.set()
                self._set_status(worker.task_run_id, TaskStatus.RUNNING, "已收到登录确认，正在继续执行")
        return self.status(platform)

    def _run_task(self, task_run_id: int, payload: StartTaskRequest, stop_event: threading.Event) -> None:
        try:
            self._log(payload.platform, task_run_id, "task_prepare", "已加载站点规则与任务参数")
            if payload.platform == Platform.ZHILIAN:
                self._run_zhilian(task_run_id, payload, stop_event)
            elif payload.platform == Platform.QIANCHENG:
                self._run_qiancheng(task_run_id, payload, stop_event)
            elif payload.platform == Platform.BOSS:
                self._run_boss(task_run_id, payload, stop_event)
            elif payload.platform == Platform.YUPAO:
                self._run_yupao(task_run_id, payload, stop_event)
            else:
                self._run_liepin(task_run_id, payload, stop_event)
            final_status = TaskStatus.STOPPED if stop_event.is_set() else TaskStatus.COMPLETED
            final_step = "任务已停止" if stop_event.is_set() else "任务执行完成"
            self._set_status(task_run_id, final_status, final_step)
        except ZhilianDailyLimitReached as exc:
            logger.info("Zhilian task ended due to daily delivery limit: %s", exc)
            self._log(payload.platform, task_run_id, "task_progress", str(exc))
            self._set_status(task_run_id, TaskStatus.COMPLETED, str(exc))
        except Exception as exc:  # noqa: BLE001
            if stop_event.is_set():
                logger.info("Task interrupted while stopping: %s", exc)
                self._log(payload.platform, task_run_id, "task_progress", f"停止过程中已中断当前浏览器动作: {exc}")
                self._set_status(task_run_id, TaskStatus.STOPPED, "任务已停止")
                return
            logger.exception("Task failed: %s", exc)
            self._log(payload.platform, task_run_id, "task_error", f"任务异常中断: {exc}", level="ERROR")
            self._set_status(task_run_id, TaskStatus.FAILED, f"任务执行失败: {exc}")
        finally:
            live_preview_service.finish_session(payload.platform, task_run_id=task_run_id)
            with self._lock:
                self._workers.pop(payload.platform.value, None)

    def _run_zhilian(self, task_run_id: int, payload: StartTaskRequest, stop_event: threading.Event) -> None:
        adapter = ZhilianAdapter()
        with self._lock:
            worker = self._workers.get(payload.platform.value)
            if worker and worker.task_run_id == task_run_id:
                worker.force_stop = adapter.request_stop
        with SessionLocal() as db:
            account = self._resolve_account(db, payload)

        def update_step(message: str) -> None:
            with SessionLocal() as db:
                task = db.get(TaskRun, task_run_id)
                if not task:
                    return
                self._assign_current_step(task, message)
                db.add(
                    TaskLog(
                        platform=payload.platform.value,
                        task_run_id=task.id,
                        event_type="task_progress",
                        message=message,
                    )
                )
                db.commit()
            live_preview_service.update_status(
                payload.platform,
                task_run_id=task_run_id,
                current_step=message,
                message=message,
            )

        def wait_for_login_confirmation() -> bool:
            while not stop_event.is_set():
                worker_state = self._workers.get(payload.platform.value)
                if worker_state and worker_state.login_confirm_event.is_set():
                    worker_state.login_confirm_event.clear()
                    return True
                time.sleep(0.5)
            return False

        def precheck_job(job_payload: dict) -> dict[str, str | bool]:
            return self._match_filters(job_payload, payload)

        def process_job(job_payload: dict) -> None:
            if stop_event.is_set():
                return
            with SessionLocal() as db:
                task = db.get(TaskRun, task_run_id)
                if not task:
                    return
                self._assign_current_step(task, f"处理中: {job_payload['title']}")
                task.processed_count += 1

                filter_result = self._match_filters(job_payload, payload)
                already_delivered = bool(job_payload.get("already_delivered", False))
                delivery_status = (
                    DeliveryStatus.SKIPPED.value
                    if not filter_result["matched"] or already_delivered
                    else DeliveryStatus.DELIVERED.value
                    if job_payload.get("delivery_success", True)
                    else DeliveryStatus.FAILED.value
                )
                status_reason = (
                    filter_result["reason"] if not filter_result["matched"] else job_payload.get("status_reason", "职位已投递")
                )

                existing_job = db.scalar(
                    select(Job).where(
                        Job.platform == payload.platform.value,
                        Job.platform_job_id == job_payload["platform_job_id"],
                    )
                )
                if existing_job:
                    existing_job.title = job_payload["title"]
                    existing_job.company_name = job_payload["company_name"]
                    if job_payload.get("salary_text"): existing_job.salary_text = job_payload.get("salary_text", "")
                    if job_payload.get("salary_min") is not None: existing_job.salary_min = job_payload.get("salary_min")
                    if job_payload.get("salary_max") is not None: existing_job.salary_max = job_payload.get("salary_max")
                    _city_val = str(job_payload.get("city") or "")
                    if _city_val and _city_val != "未标注": existing_job.city = _city_val
                    _exp_val = str(job_payload.get("experience_text") or "")
                    if _exp_val and _exp_val != "不限": existing_job.experience_text = _exp_val
                    _edu_val = str(job_payload.get("education_text") or "")
                    if _edu_val and _edu_val != "未标注": existing_job.education_text = _edu_val
                    if job_payload.get("description"): existing_job.description = job_payload["description"]
                    if job_payload.get("detail_url"): existing_job.detail_url = job_payload["detail_url"]
                    if job_payload.get("tags"): existing_job.tags = job_payload.get("tags", [])
                    existing_job.latest_status = delivery_status
                    existing_job.task_run_id = task.id
                    job = existing_job
                else:
                    job = Job(
                        platform=payload.platform.value,
                        platform_job_id=job_payload["platform_job_id"],
                        title=job_payload["title"],
                        company_name=job_payload["company_name"],
                        salary_text=job_payload["salary_text"],
                        salary_min=job_payload.get("salary_min"),
                        salary_max=job_payload.get("salary_max"),
                        city=job_payload.get("city", payload.cities[0] if payload.cities else ""),
                        experience_text=job_payload.get("experience_text", "不限"),
                        education_text=job_payload.get("education_text", "未标注"),
                        description=job_payload["description"],
                        detail_url=job_payload["detail_url"],
                        tags=job_payload.get("tags", []),
                        latest_status=delivery_status,
                        task_run_id=task.id,
                    )
                    db.add(job)
                    db.flush()

                delivery = DeliveryRecord(
                    job_id=job.id,
                    task_run_id=task.id,
                    account_id=task.account_id,
                    platform=payload.platform.value,
                    status=delivery_status,
                    status_reason=status_reason,
                    send_status=ChatSendStatus.SKIPPED.value,
                    chat_hr_name="",
                    chat_sent=False,
                    chat_message="",
                )
                db.add(delivery)
                if not filter_result["matched"]:
                    task.failed_count += 1
                elif already_delivered:
                    pass
                elif job_payload.get("delivery_success", True):
                    task.delivered_count += 1
                else:
                    task.failed_count += 1
                db.add(
                    TaskLog(
                        platform=payload.platform.value,
                        task_run_id=task.id,
                        event_type="job_processed",
                        message=(
                            f" {job_payload.get('search_term', '')} | "
                            f"{job.title} / {job.company_name} / {delivery.status} / {status_reason}"
                        ),
                        extra_json={
                            "detail_url": job.detail_url,
                            "platform_job_id": job.platform_job_id,
                            "search_term": job_payload.get("search_term"),
                            "reason": status_reason,
                        },
                    )
                )
                db.commit()
            time.sleep(1)

        if settings.automation_mode == "playwright":
            import asyncio

            if sys.platform == "win32":
                loop = asyncio.ProactorEventLoop()
                try:
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(
                        adapter.run_search_flow(
                            payload.search_terms,
                            account.profile_dir,
                            process_job,
                            stop_event.is_set,
                            update_step,
                            wait_for_login_confirmation,
                            entry_url=payload.manual_entry_url if payload.use_manual_entry_url else None,
                            task_run_id=task_run_id,
                            headless=payload.headless_enabled,
                            live_preview_enabled=payload.live_preview_enabled,
                            live_preview_interval_ms=payload.live_preview_interval_ms,
                            delivery_interval_seconds=payload.delivery_interval_seconds,
                            max_deliveries_per_run=payload.max_deliveries_per_run,
                        )
                    )
                finally:
                    loop.close()
                    asyncio.set_event_loop(None)
            else:
                asyncio.run(
                    adapter.run_search_flow(
                        payload.search_terms,
                        account.profile_dir,
                        process_job,
                        stop_event.is_set,
                        update_step,
                        wait_for_login_confirmation,
                        entry_url=payload.manual_entry_url if payload.use_manual_entry_url else None,
                        task_run_id=task_run_id,
                        headless=payload.headless_enabled,
                        live_preview_enabled=payload.live_preview_enabled,
                        live_preview_interval_ms=payload.live_preview_interval_ms,
                        delivery_interval_seconds=payload.delivery_interval_seconds,
                        max_deliveries_per_run=payload.max_deliveries_per_run,
                    )
                )
        else:
            self._set_status(task_run_id, TaskStatus.RUNNING, "模拟执行智联搜索与投递流程")
            adapter.run_sync_mock(",".join(payload.search_terms), process_job, stop_event.is_set)

    def _run_qiancheng(self, task_run_id: int, payload: StartTaskRequest, stop_event: threading.Event) -> None:
        adapter = QianchengAdapter()
        with self._lock:
            worker = self._workers.get(payload.platform.value)
            if worker and worker.task_run_id == task_run_id:
                worker.force_stop = adapter.request_stop
        with SessionLocal() as db:
            account = self._resolve_account(db, payload)

        def update_step(message: str) -> None:
            with SessionLocal() as db:
                task = db.get(TaskRun, task_run_id)
                if not task:
                    return
                self._assign_current_step(task, message)
                db.add(
                    TaskLog(
                        platform=payload.platform.value,
                        task_run_id=task.id,
                        event_type="task_progress",
                        message=message,
                    )
                )
                db.commit()
            live_preview_service.update_status(
                payload.platform,
                task_run_id=task_run_id,
                current_step=message,
                message=message,
            )

        def wait_for_login_confirmation() -> bool:
            while not stop_event.is_set():
                worker_state = self._workers.get(payload.platform.value)
                if worker_state and worker_state.login_confirm_event.is_set():
                    worker_state.login_confirm_event.clear()
                    return True
                time.sleep(0.5)
            return False

        def precheck_job(job_payload: dict) -> dict[str, str | bool]:
            return self._match_filters(job_payload, payload)

        def process_job(job_payload: dict) -> None:
            if stop_event.is_set():
                return
            with SessionLocal() as db:
                task = db.get(TaskRun, task_run_id)
                if not task:
                    return
                self._assign_current_step(task, f"处理中: {job_payload['title']}")
                task.processed_count += 1

                filter_result = self._match_filters(job_payload, payload)
                already_delivered = bool(job_payload.get("already_delivered", False))
                delivery_status = (
                    DeliveryStatus.SKIPPED.value
                    if not filter_result["matched"] or already_delivered
                    else DeliveryStatus.DELIVERED.value
                    if job_payload.get("delivery_success", True)
                    else DeliveryStatus.FAILED.value
                )
                status_reason = (
                    filter_result["reason"] if not filter_result["matched"] else job_payload.get("status_reason", "职位已投递")
                )

                existing_job = db.scalar(
                    select(Job).where(
                        Job.platform == payload.platform.value,
                        Job.platform_job_id == job_payload["platform_job_id"],
                    )
                )
                if existing_job:
                    existing_job.title = job_payload["title"]
                    existing_job.company_name = job_payload["company_name"]
                    if job_payload.get("salary_text"): existing_job.salary_text = job_payload.get("salary_text", "")
                    if job_payload.get("salary_min") is not None: existing_job.salary_min = job_payload.get("salary_min")
                    if job_payload.get("salary_max") is not None: existing_job.salary_max = job_payload.get("salary_max")
                    _city_val = str(job_payload.get("city") or "")
                    if _city_val and _city_val != "未标注": existing_job.city = _city_val
                    _exp_val = str(job_payload.get("experience_text") or "")
                    if _exp_val and _exp_val != "不限": existing_job.experience_text = _exp_val
                    _edu_val = str(job_payload.get("education_text") or "")
                    if _edu_val and _edu_val != "未标注": existing_job.education_text = _edu_val
                    if job_payload.get("description"): existing_job.description = job_payload["description"]
                    if job_payload.get("detail_url"): existing_job.detail_url = job_payload["detail_url"]
                    if job_payload.get("tags"): existing_job.tags = job_payload.get("tags", [])
                    existing_job.latest_status = delivery_status
                    existing_job.task_run_id = task.id
                    job = existing_job
                else:
                    job = Job(
                        platform=payload.platform.value,
                        platform_job_id=job_payload["platform_job_id"],
                        title=job_payload["title"],
                        company_name=job_payload["company_name"],
                        salary_text=job_payload["salary_text"],
                        salary_min=job_payload.get("salary_min"),
                        salary_max=job_payload.get("salary_max"),
                        city=job_payload.get("city", payload.cities[0] if payload.cities else ""),
                        experience_text=job_payload.get("experience_text", "不限"),
                        education_text=job_payload.get("education_text", "未标注"),
                        description=job_payload["description"],
                        detail_url=job_payload["detail_url"],
                        tags=job_payload.get("tags", []),
                        latest_status=delivery_status,
                        task_run_id=task.id,
                    )
                    db.add(job)
                    db.flush()

                delivery = DeliveryRecord(
                    job_id=job.id,
                    task_run_id=task.id,
                    account_id=task.account_id,
                    platform=payload.platform.value,
                    status=delivery_status,
                    status_reason=status_reason,
                    send_status=ChatSendStatus.SKIPPED.value,
                    chat_hr_name="",
                    chat_sent=False,
                    chat_message="",
                )
                db.add(delivery)
                if not filter_result["matched"]:
                    task.failed_count += 1
                elif already_delivered:
                    pass
                elif job_payload.get("delivery_success", True):
                    task.delivered_count += 1
                else:
                    task.failed_count += 1
                db.add(
                    TaskLog(
                        platform=payload.platform.value,
                        task_run_id=task.id,
                        event_type="job_processed",
                        message=(
                            f" {job_payload.get('search_term', '')} | "
                            f"{job.title} / {job.company_name} / {delivery.status} / {status_reason}"
                        ),
                        extra_json={
                            "detail_url": job.detail_url,
                            "platform_job_id": job.platform_job_id,
                            "search_term": job_payload.get("search_term"),
                            "reason": status_reason,
                        },
                    )
                )
                db.commit()
            time.sleep(1)

        if settings.automation_mode == "playwright":
            import asyncio

            if sys.platform == "win32":
                loop = asyncio.ProactorEventLoop()
                try:
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(
                        adapter.run_search_flow(
                            payload.search_terms,
                            account.profile_dir,
                            process_job,
                            stop_event.is_set,
                            update_step,
                            wait_for_login_confirmation,
                            entry_url=payload.manual_entry_url if payload.use_manual_entry_url else None,
                            task_run_id=task_run_id,
                            headless=payload.headless_enabled,
                            live_preview_enabled=payload.live_preview_enabled,
                            live_preview_interval_ms=payload.live_preview_interval_ms,
                            delivery_interval_seconds=payload.delivery_interval_seconds,
                            max_deliveries_per_run=payload.max_deliveries_per_run,
                        )
                    )
                finally:
                    loop.close()
                    asyncio.set_event_loop(None)
            else:
                asyncio.run(
                    adapter.run_search_flow(
                        payload.search_terms,
                        account.profile_dir,
                        process_job,
                        stop_event.is_set,
                        update_step,
                        wait_for_login_confirmation,
                        entry_url=payload.manual_entry_url if payload.use_manual_entry_url else None,
                        task_run_id=task_run_id,
                        headless=payload.headless_enabled,
                        live_preview_enabled=payload.live_preview_enabled,
                        live_preview_interval_ms=payload.live_preview_interval_ms,
                        delivery_interval_seconds=payload.delivery_interval_seconds,
                        max_deliveries_per_run=payload.max_deliveries_per_run,
                    )
                )
        else:
            self._set_status(task_run_id, TaskStatus.RUNNING, "模拟执行前程无忧搜索与投递流程")
            adapter.run_sync_mock(",".join(payload.search_terms), process_job, stop_event.is_set)

    def _run_liepin(self, task_run_id: int, payload: StartTaskRequest, stop_event: threading.Event) -> None:
        adapter = LiepinAdapter()
        with self._lock:
            worker = self._workers.get(payload.platform.value)
            if worker and worker.task_run_id == task_run_id:
                worker.force_stop = adapter.request_stop
        with SessionLocal() as db:
            account = self._resolve_account(db, payload)

        def update_step(message: str) -> None:
            with SessionLocal() as db:
                task = db.get(TaskRun, task_run_id)
                if not task:
                    return
                self._assign_current_step(task, message)
                db.add(
                    TaskLog(
                        platform=payload.platform.value,
                        task_run_id=task.id,
                        event_type="task_progress",
                        message=message,
                    )
                )
                db.commit()
            live_preview_service.update_status(
                payload.platform,
                task_run_id=task_run_id,
                current_step=message,
                message=message,
            )

        def wait_for_login_confirmation() -> bool:
            while not stop_event.is_set():
                worker_state = self._workers.get(payload.platform.value)
                if worker_state and worker_state.login_confirm_event.is_set():
                    worker_state.login_confirm_event.clear()
                    return True
                time.sleep(0.5)
            return False

        def precheck_job(job_payload: dict) -> dict[str, str | bool]:
            return self._match_filters(job_payload, payload)

        def process_job(job_payload: dict) -> None:
            if stop_event.is_set():
                return
            with SessionLocal() as db:
                task = db.get(TaskRun, task_run_id)
                if not task:
                    return
                self._assign_current_step(task, f"处理中: {job_payload['title']}")
                task.processed_count += 1

                filter_result = self._match_filters(job_payload, payload)
                already_delivered = bool(job_payload.get("already_delivered", False))
                delivery_status = (
                    DeliveryStatus.SKIPPED.value
                    if not filter_result["matched"] or already_delivered
                    else DeliveryStatus.DELIVERED.value
                    if job_payload.get("delivery_success", False)
                    else DeliveryStatus.FAILED.value
                )
                status_reason = (
                    filter_result["reason"]
                    if not filter_result["matched"]
                    else job_payload.get("status_reason", "猎聘继续聊完成")
                )

                existing_job = db.scalar(
                    select(Job).where(
                        Job.platform == payload.platform.value,
                        Job.platform_job_id == job_payload["platform_job_id"],
                    )
                )
                if existing_job:
                    existing_job.title = job_payload["title"]
                    existing_job.company_name = job_payload["company_name"]
                    if job_payload.get("salary_text"): existing_job.salary_text = job_payload.get("salary_text", "")
                    if job_payload.get("salary_min") is not None: existing_job.salary_min = job_payload.get("salary_min")
                    if job_payload.get("salary_max") is not None: existing_job.salary_max = job_payload.get("salary_max")
                    _city_val = str(job_payload.get("city") or "")
                    if _city_val and _city_val != "未标注": existing_job.city = _city_val
                    _exp_val = str(job_payload.get("experience_text") or "")
                    if _exp_val and _exp_val != "不限": existing_job.experience_text = _exp_val
                    _edu_val = str(job_payload.get("education_text") or "")
                    if _edu_val and _edu_val != "未标注": existing_job.education_text = _edu_val
                    if job_payload.get("description"): existing_job.description = job_payload.get("description", "")
                    if job_payload.get("detail_url"): existing_job.detail_url = job_payload.get("detail_url", "")
                    if job_payload.get("tags"): existing_job.tags = job_payload.get("tags", [])
                    existing_job.latest_status = delivery_status
                    existing_job.task_run_id = task.id
                    job = existing_job
                else:
                    job = Job(
                        platform=payload.platform.value,
                        platform_job_id=job_payload["platform_job_id"],
                        title=job_payload["title"],
                        company_name=job_payload["company_name"],
                        salary_text=job_payload.get("salary_text", "未标注"),
                        salary_min=job_payload.get("salary_min"),
                        salary_max=job_payload.get("salary_max"),
                        city=job_payload.get("city", payload.cities[0] if payload.cities else ""),
                        experience_text=job_payload.get("experience_text", "不限"),
                        education_text=job_payload.get("education_text", "未标注"),
                        description=job_payload.get("description", ""),
                        detail_url=job_payload.get("detail_url", ""),
                        tags=job_payload.get("tags", []),
                        latest_status=delivery_status,
                        task_run_id=task.id,
                    )
                    db.add(job)
                    db.flush()

                delivery = DeliveryRecord(
                    job_id=job.id,
                    task_run_id=task.id,
                    account_id=task.account_id,
                    platform=payload.platform.value,
                    status=delivery_status,
                    status_reason=status_reason,
                    send_status=self._initial_chat_send_status(payload, job_payload, delivery_status),
                    chat_hr_name=str(job_payload.get("recruiter_name", "") or ""),
                    chat_sent=bool(job_payload.get("chat_sent", False)),
                    chat_message=str(job_payload.get("chat_message", "") or ""),
                )
                db.add(delivery)
                if not filter_result["matched"]:
                    task.failed_count += 1
                elif already_delivered:
                    pass
                elif job_payload.get("delivery_success", False):
                    task.delivered_count += 1
                else:
                    task.failed_count += 1

                db.add(
                    TaskLog(
                        platform=payload.platform.value,
                        task_run_id=task.id,
                        event_type="job_processed",
                        message=(
                            f" {job_payload.get('search_term', '')} | "
                            f"{job.title} / {job.company_name} / {delivery.status} / {status_reason}"
                        ),
                        extra_json={
                            "detail_url": job.detail_url,
                            "platform_job_id": job.platform_job_id,
                            "search_term": job_payload.get("search_term"),
                            "reason": status_reason,
                            "chat_sent": bool(job_payload.get("chat_sent", False)),
                            "send_status": delivery.send_status,
                        },
                    )
                )
                db.commit()
            time.sleep(1)

        if settings.automation_mode == "playwright":
            import asyncio

            if sys.platform == "win32":
                loop = asyncio.ProactorEventLoop()
                try:
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(
                        adapter.run_search_flow(
                            payload.search_terms,
                            account.profile_dir,
                            process_job,
                            stop_event.is_set,
                            update_step,
                            wait_for_login_confirmation,
                            entry_url=payload.manual_entry_url if payload.use_manual_entry_url else None,
                            task_run_id=task_run_id,
                            headless=payload.headless_enabled,
                            live_preview_enabled=payload.live_preview_enabled,
                            live_preview_interval_ms=payload.live_preview_interval_ms,
                            delivery_interval_seconds=payload.delivery_interval_seconds,
                            max_deliveries_per_run=payload.max_deliveries_per_run,
                            auto_send_chat=payload.auto_send_chat,
                            use_chat_template=payload.use_chat_template,
                            chat_message_template=payload.chat_template,
                            enable_image_resume=payload.enable_image_resume,
                            image_resume_path=payload.image_resume_path,
                        )
                    )
                finally:
                    loop.close()
                    asyncio.set_event_loop(None)
            else:
                asyncio.run(
                    adapter.run_search_flow(
                        payload.search_terms,
                        account.profile_dir,
                        process_job,
                        stop_event.is_set,
                        update_step,
                        wait_for_login_confirmation,
                        entry_url=payload.manual_entry_url if payload.use_manual_entry_url else None,
                        task_run_id=task_run_id,
                        headless=payload.headless_enabled,
                        live_preview_enabled=payload.live_preview_enabled,
                        live_preview_interval_ms=payload.live_preview_interval_ms,
                        delivery_interval_seconds=payload.delivery_interval_seconds,
                        max_deliveries_per_run=payload.max_deliveries_per_run,
                        auto_send_chat=payload.auto_send_chat,
                        use_chat_template=payload.use_chat_template,
                        chat_message_template=payload.chat_template,
                        enable_image_resume=payload.enable_image_resume,
                        image_resume_path=payload.image_resume_path,
                    )
                )
        else:
            self._set_status(task_run_id, TaskStatus.RUNNING, "模拟执行猎聘搜索与继续聊流程")
            adapter.run_sync_mock(",".join(payload.search_terms), process_job, stop_event.is_set)

    def _run_yupao(self, task_run_id: int, payload: StartTaskRequest, stop_event: threading.Event) -> None:
        adapter = YupaoAdapter()
        with self._lock:
            worker = self._workers.get(payload.platform.value)
            if worker and worker.task_run_id == task_run_id:
                worker.force_stop = adapter.request_stop
        with SessionLocal() as db:
            account = self._resolve_account(db, payload)

        def update_step(message: str) -> None:
            with SessionLocal() as db:
                task = db.get(TaskRun, task_run_id)
                if not task:
                    return
                self._assign_current_step(task, message)
                db.add(
                    TaskLog(
                        platform=payload.platform.value,
                        task_run_id=task.id,
                        event_type="task_progress",
                        message=message,
                    )
                )
                db.commit()
            live_preview_service.update_status(
                payload.platform,
                task_run_id=task_run_id,
                current_step=message,
                message=message,
            )

        def wait_for_login_confirmation() -> bool:
            while not stop_event.is_set():
                worker_state = self._workers.get(payload.platform.value)
                if worker_state and worker_state.login_confirm_event.is_set():
                    worker_state.login_confirm_event.clear()
                    return True
                time.sleep(0.5)
            return False

        def precheck_job(job_payload: dict) -> dict[str, str | bool]:
            return self._match_filters(job_payload, payload)

        def process_job(job_payload: dict) -> None:
            if stop_event.is_set():
                return
            with SessionLocal() as db:
                task = db.get(TaskRun, task_run_id)
                if not task:
                    return
                self._assign_current_step(task, f"处理中: {job_payload['title']}")
                task.processed_count += 1

                filter_result = self._match_filters(job_payload, payload)
                already_delivered = bool(job_payload.get("already_delivered", False))
                delivery_status = (
                    DeliveryStatus.SKIPPED.value
                    if not filter_result["matched"] or already_delivered
                    else DeliveryStatus.DELIVERED.value
                    if job_payload.get("delivery_success", False)
                    else DeliveryStatus.FAILED.value
                )
                status_reason = (
                    filter_result["reason"]
                    if not filter_result["matched"]
                    else job_payload.get("status_reason", "鱼泡沟通流程完成")
                )

                existing_job = db.scalar(
                    select(Job).where(
                        Job.platform == payload.platform.value,
                        Job.platform_job_id == job_payload["platform_job_id"],
                    )
                )
                if existing_job:
                    existing_job.title = job_payload["title"]
                    existing_job.company_name = job_payload["company_name"]
                    if job_payload.get("salary_text"): existing_job.salary_text = job_payload.get("salary_text", "")
                    if job_payload.get("salary_min") is not None: existing_job.salary_min = job_payload.get("salary_min")
                    if job_payload.get("salary_max") is not None: existing_job.salary_max = job_payload.get("salary_max")
                    _city_val = str(job_payload.get("city") or "")
                    if _city_val and _city_val != "未标注": existing_job.city = _city_val
                    _exp_val = str(job_payload.get("experience_text") or "")
                    if _exp_val and _exp_val != "不限": existing_job.experience_text = _exp_val
                    _edu_val = str(job_payload.get("education_text") or "")
                    if _edu_val and _edu_val != "未标注": existing_job.education_text = _edu_val
                    if job_payload.get("description"): existing_job.description = job_payload.get("description", "")
                    if job_payload.get("detail_url"): existing_job.detail_url = job_payload.get("detail_url", "")
                    if job_payload.get("tags"): existing_job.tags = job_payload.get("tags", [])
                    existing_job.latest_status = delivery_status
                    existing_job.task_run_id = task.id
                    job = existing_job
                else:
                    job = Job(
                        platform=payload.platform.value,
                        platform_job_id=job_payload["platform_job_id"],
                        title=job_payload["title"],
                        company_name=job_payload["company_name"],
                        salary_text=job_payload.get("salary_text", "未标注"),
                        salary_min=job_payload.get("salary_min"),
                        salary_max=job_payload.get("salary_max"),
                        city=job_payload.get("city", payload.cities[0] if payload.cities else ""),
                        experience_text=job_payload.get("experience_text", "不限"),
                        education_text=job_payload.get("education_text", "未标注"),
                        description=job_payload.get("description", ""),
                        detail_url=job_payload.get("detail_url", ""),
                        tags=job_payload.get("tags", []),
                        latest_status=delivery_status,
                        task_run_id=task.id,
                    )
                    db.add(job)
                    db.flush()

                delivery = DeliveryRecord(
                    job_id=job.id,
                    task_run_id=task.id,
                    account_id=task.account_id,
                    platform=payload.platform.value,
                    status=delivery_status,
                    status_reason=status_reason,
                    send_status=self._initial_chat_send_status(payload, job_payload, delivery_status),
                    chat_hr_name=str(job_payload.get("recruiter_name", "") or ""),
                    chat_sent=bool(job_payload.get("chat_sent", False)),
                    chat_message=str(job_payload.get("chat_message", "") or ""),
                )
                db.add(delivery)
                if not filter_result["matched"]:
                    task.failed_count += 1
                elif already_delivered:
                    pass
                elif job_payload.get("delivery_success", False):
                    task.delivered_count += 1
                else:
                    task.failed_count += 1

                db.add(
                    TaskLog(
                        platform=payload.platform.value,
                        task_run_id=task.id,
                        event_type="job_processed",
                        message=(
                            f" {job_payload.get('search_term', '')} | "
                            f"{job.title} / {job.company_name} / {delivery.status} / {status_reason}"
                        ),
                        extra_json={
                            "detail_url": job.detail_url,
                            "platform_job_id": job.platform_job_id,
                            "search_term": job_payload.get("search_term"),
                            "reason": status_reason,
                            "chat_sent": bool(job_payload.get("chat_sent", False)),
                            "send_status": delivery.send_status,
                        },
                    )
                )
                db.commit()
            time.sleep(1)

        if settings.automation_mode == "playwright":
            import asyncio

            if sys.platform == "win32":
                loop = asyncio.ProactorEventLoop()
                try:
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(
                        adapter.run_search_flow(
                            payload.search_terms,
                            account.profile_dir,
                            process_job,
                            stop_event.is_set,
                            update_step,
                            wait_for_login_confirmation,
                            precheck_job,
                            entry_url=payload.manual_entry_url if payload.use_manual_entry_url else settings.yupao_entry_url,
                            task_run_id=task_run_id,
                            headless=payload.headless_enabled,
                            live_preview_enabled=payload.live_preview_enabled,
                            live_preview_interval_ms=payload.live_preview_interval_ms,
                            delivery_interval_seconds=payload.delivery_interval_seconds,
                            max_deliveries_per_run=payload.max_deliveries_per_run,
                            auto_send_chat=payload.auto_send_chat,
                            use_chat_template=payload.use_chat_template,
                            chat_message_template=payload.chat_template,
                            enable_image_resume=payload.enable_image_resume,
                            image_resume_path=payload.image_resume_path,
                        )
                    )
                finally:
                    loop.close()
                    asyncio.set_event_loop(None)
            else:
                asyncio.run(
                    adapter.run_search_flow(
                        payload.search_terms,
                        account.profile_dir,
                        process_job,
                        stop_event.is_set,
                        update_step,
                        wait_for_login_confirmation,
                        precheck_job,
                        entry_url=payload.manual_entry_url if payload.use_manual_entry_url else settings.yupao_entry_url,
                        task_run_id=task_run_id,
                        headless=payload.headless_enabled,
                        live_preview_enabled=payload.live_preview_enabled,
                        live_preview_interval_ms=payload.live_preview_interval_ms,
                        delivery_interval_seconds=payload.delivery_interval_seconds,
                        max_deliveries_per_run=payload.max_deliveries_per_run,
                        auto_send_chat=payload.auto_send_chat,
                        use_chat_template=payload.use_chat_template,
                        chat_message_template=payload.chat_template,
                        enable_image_resume=payload.enable_image_resume,
                        image_resume_path=payload.image_resume_path,
                    )
                )
        else:
            self._set_status(task_run_id, TaskStatus.RUNNING, "模拟执行鱼泡列表与沟通流程")
            adapter.run_sync_mock(payload.manual_entry_url or settings.yupao_entry_url, process_job, stop_event.is_set)

    def _run_boss(self, task_run_id: int, payload: StartTaskRequest, stop_event: threading.Event) -> None:
        adapter = BossAdapter()
        with self._lock:
            worker = self._workers.get(payload.platform.value)
            if worker and worker.task_run_id == task_run_id:
                worker.force_stop = adapter.request_stop
        with SessionLocal() as db:
            account = self._resolve_account(db, payload)

        def update_step(message: str) -> None:
            with SessionLocal() as db:
                task = db.get(TaskRun, task_run_id)
                if not task:
                    return
                self._assign_current_step(task, message)
                db.add(
                    TaskLog(
                        platform=payload.platform.value,
                        task_run_id=task.id,
                        event_type="task_progress",
                        message=message,
                    )
                )
                db.commit()
            live_preview_service.update_status(
                payload.platform,
                task_run_id=task_run_id,
                current_step=message,
                message=message,
            )

        def wait_for_login_confirmation() -> bool:
            while not stop_event.is_set():
                worker_state = self._workers.get(payload.platform.value)
                if worker_state and worker_state.login_confirm_event.is_set():
                    worker_state.login_confirm_event.clear()
                    return True
                time.sleep(0.5)
            return False

        def precheck_job(job_payload: dict) -> dict[str, str | bool]:
            return self._match_filters(job_payload, payload)

        def process_job(job_payload: dict) -> None:
            if stop_event.is_set():
                return
            with SessionLocal() as db:
                task = db.get(TaskRun, task_run_id)
                if not task:
                    return
                self._assign_current_step(task, f"处理中: {job_payload['title']}")
                task.processed_count += 1

                matched = bool(job_payload.get("matched", True))
                delivery_status = (
                    DeliveryStatus.DELIVERED.value
                    if matched and job_payload.get("delivery_success", False)
                    else DeliveryStatus.SKIPPED.value if not matched else DeliveryStatus.FAILED.value
                )
                status_reason = str(job_payload.get("status_reason", "Boss 沟通完成"))

                existing_job = db.scalar(
                    select(Job).where(
                        Job.platform == payload.platform.value,
                        Job.platform_job_id == job_payload["platform_job_id"],
                    )
                )
                if existing_job:
                    existing_job.title = job_payload["title"]
                    existing_job.company_name = job_payload["company_name"]
                    if job_payload.get("salary_text"): existing_job.salary_text = job_payload.get("salary_text", "")
                    if job_payload.get("salary_min") is not None: existing_job.salary_min = job_payload.get("salary_min")
                    if job_payload.get("salary_max") is not None: existing_job.salary_max = job_payload.get("salary_max")
                    _city_val = str(job_payload.get("city") or "")
                    if _city_val and _city_val != "未标注": existing_job.city = _city_val
                    _exp_val = str(job_payload.get("experience_text") or "")
                    if _exp_val and _exp_val != "不限": existing_job.experience_text = _exp_val
                    _edu_val = str(job_payload.get("education_text") or "")
                    if _edu_val and _edu_val != "未标注": existing_job.education_text = _edu_val
                    if job_payload.get("description"): existing_job.description = job_payload.get("description", "")
                    if job_payload.get("detail_url"): existing_job.detail_url = job_payload.get("detail_url", "")
                    if job_payload.get("tags"): existing_job.tags = job_payload.get("tags", [])
                    existing_job.latest_status = delivery_status
                    existing_job.task_run_id = task.id
                    job = existing_job
                else:
                    job = Job(
                        platform=payload.platform.value,
                        platform_job_id=job_payload["platform_job_id"],
                        title=job_payload["title"],
                        company_name=job_payload["company_name"],
                        salary_text=job_payload.get("salary_text", "未标注"),
                        salary_min=job_payload.get("salary_min"),
                        salary_max=job_payload.get("salary_max"),
                        city=job_payload.get("city", ""),
                        experience_text=job_payload.get("experience_text", "不限"),
                        education_text=job_payload.get("education_text", "未标注"),
                        description=job_payload.get("description", ""),
                        detail_url=job_payload.get("detail_url", ""),
                        tags=job_payload.get("tags", []),
                        latest_status=delivery_status,
                        task_run_id=task.id,
                    )
                    db.add(job)
                    db.flush()

                delivery = DeliveryRecord(
                    job_id=job.id,
                    task_run_id=task.id,
                    account_id=task.account_id,
                    platform=payload.platform.value,
                    status=delivery_status,
                    status_reason=status_reason,
                    send_status=self._initial_chat_send_status(payload, job_payload, delivery_status),
                    chat_hr_name="",
                    chat_sent=bool(job_payload.get("chat_sent", False)),
                    chat_message=job_payload.get("chat_message", ""),
                )
                db.add(delivery)

                if delivery_status == DeliveryStatus.DELIVERED.value:
                    task.delivered_count += 1
                elif delivery_status == DeliveryStatus.FAILED.value:
                    task.failed_count += 1

                db.add(
                    TaskLog(
                        platform=payload.platform.value,
                        task_run_id=task.id,
                        event_type="job_processed",
                        message=(
                            f" {job_payload.get('search_term', '')} | "
                            f"{job.title} / {job.company_name} / {delivery.status} / {status_reason}"
                        ),
                        extra_json={
                            "detail_url": job.detail_url,
                            "platform_job_id": job.platform_job_id,
                            "search_term": job_payload.get("search_term"),
                            "reason": status_reason,
                            "chat_sent": bool(job_payload.get("chat_sent", False)),
                        },
                    )
                )
                db.commit()
            time.sleep(1)

        def process_chat_event(event: dict) -> None:
            if stop_event.is_set():
                return
            event_type = event.get("type", "boss_chat_event")
            hr_key = event.get("hr_key", "")
            hr_name = event.get("hr_name", "")
            company_name = event.get("company_name", "")
            job_title = event.get("job_title", "")
            message = event.get("message", "")
            source_message = event.get("source_message", "")
            platform_job_id = event.get("platform_job_id", "")
            detail_url = event.get("detail_url", "")
            chat_sent = bool(event.get("chat_sent", False))
            send_status = str(event.get("send_status", "") or "").strip()
            weak_match_key = self._normalize_chat_match_key(company_name, job_title, hr_name)

            with SessionLocal() as db:
                task = db.get(TaskRun, task_run_id)
                if not task:
                    return

                if event_type == "chat_delivery_updated":
                    delivery = self._find_chat_delivery(
                        db,
                        task.id,
                        payload.platform.value,
                        platform_job_id=platform_job_id,
                        job_title=job_title,
                        company_name=company_name,
                        hr_name=hr_name,
                        weak_match_key=weak_match_key,
                    )
                    if delivery:
                        resolved_send_status = send_status or (
                            ChatSendStatus.SENT.value if chat_sent else ChatSendStatus.FAILED.value
                        )
                        delivery.send_status = resolved_send_status
                        delivery.chat_hr_name = hr_name or delivery.chat_hr_name
                        delivery.chat_sent = chat_sent
                        delivery.chat_message = message or delivery.chat_message

                db.add(
                    TaskLog(
                        platform=payload.platform.value,
                        task_run_id=task.id,
                        event_type=event_type,
                        message=f"Boss 聊天事件: {hr_name} / {company_name} / {message}",
                        extra_json={
                            "hr_key": hr_key,
                            "hr_name": hr_name,
                            "company_name": company_name,
                            "job_title": job_title,
                            "platform_job_id": platform_job_id,
                            "detail_url": detail_url,
                            "message": message,
                            "chat_sent": chat_sent,
                            "send_status": send_status,
                            "source_message": source_message,
                        },
                    )
                )
                db.commit()

        if settings.automation_mode == "playwright":
            import asyncio

            if sys.platform == "win32":
                loop = asyncio.ProactorEventLoop()
                try:
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(
                        adapter.run_hybrid_flow(
                            account.profile_dir,
                            jobs_entry_url=payload.manual_entry_url if payload.use_manual_entry_url else settings.boss_jobs_url,
                            search_terms=payload.search_terms,
                            keywords=payload.keywords,
                            cities=payload.cities,
                            auto_send_chat=payload.auto_send_chat,
                            use_chat_template=payload.use_chat_template,
                            chat_message_template=payload.chat_template,
                            enable_common_phrases=payload.enable_common_phrases,
                            common_phrase_indexes=payload.common_phrase_indexes,
                            enable_image_resume=payload.enable_image_resume,
                            image_resume_path=payload.image_resume_path,
                            auto_agree_wechat_card=payload.auto_agree_wechat_card,
                            boss_first_greeting_only=payload.boss_first_greeting_only,
                            enable_follow_up_reply=payload.enable_follow_up_reply,
                            follow_up_reply_rules=[rule.model_dump() for rule in payload.boss_reply_rules],
                            delivery_interval_seconds=payload.delivery_interval_seconds,
                            max_deliveries_per_run=payload.max_deliveries_per_run,
                            should_stop=stop_event.is_set,
                            status_callback=update_step,
                            job_callback=process_job,
                            job_precheck_callback=precheck_job,
                            chat_event_callback=process_chat_event,
                            confirm_login_callback=wait_for_login_confirmation,
                            task_run_id=task_run_id,
                            headless=payload.headless_enabled,
                            live_preview_enabled=payload.live_preview_enabled,
                            live_preview_interval_ms=payload.live_preview_interval_ms,
                        )
                    )
                finally:
                    loop.close()
                    asyncio.set_event_loop(None)
            else:
                asyncio.run(
                    adapter.run_hybrid_flow(
                        account.profile_dir,
                        jobs_entry_url=payload.manual_entry_url if payload.use_manual_entry_url else settings.boss_jobs_url,
                        search_terms=payload.search_terms,
                        keywords=payload.keywords,
                        cities=payload.cities,
                        auto_send_chat=payload.auto_send_chat,
                        use_chat_template=payload.use_chat_template,
                        chat_message_template=payload.chat_template,
                        enable_common_phrases=payload.enable_common_phrases,
                        common_phrase_indexes=payload.common_phrase_indexes,
                        enable_image_resume=payload.enable_image_resume,
                        image_resume_path=payload.image_resume_path,
                        auto_agree_wechat_card=payload.auto_agree_wechat_card,
                        boss_first_greeting_only=payload.boss_first_greeting_only,
                        enable_follow_up_reply=payload.enable_follow_up_reply,
                        follow_up_reply_rules=[rule.model_dump() for rule in payload.boss_reply_rules],
                        delivery_interval_seconds=payload.delivery_interval_seconds,
                        max_deliveries_per_run=payload.max_deliveries_per_run,
                        should_stop=stop_event.is_set,
                        status_callback=update_step,
                        job_callback=process_job,
                        job_precheck_callback=precheck_job,
                        chat_event_callback=process_chat_event,
                        confirm_login_callback=wait_for_login_confirmation,
                        task_run_id=task_run_id,
                        headless=payload.headless_enabled,
                        live_preview_enabled=payload.live_preview_enabled,
                        live_preview_interval_ms=payload.live_preview_interval_ms,
                    )
                )
        else:
            self._set_status(task_run_id, TaskStatus.RUNNING, "模拟执行 Boss 海投与聊天流程")

    def _match_filters(self, job_payload: dict, payload: StartTaskRequest) -> dict[str, str | bool]:
        shared_result = self._match_cross_account_dedupe(job_payload, payload)
        if shared_result is not None:
            return shared_result

        if payload.cities:
            job_city = (job_payload.get("city") or "").strip()
            if job_city and not any(city.strip() and city.strip() in job_city for city in payload.cities):
                return {"matched": False, "reason": f"城市不匹配: {job_city}"}

        company_blacklist_result = self._match_company_blacklist(job_payload, payload)
        if company_blacklist_result is not None:
            return company_blacklist_result

        job_title_blacklist_result = self._match_job_title_blacklist(job_payload, payload)
        if job_title_blacklist_result is not None:
            return job_title_blacklist_result

        if payload.keywords:
            if payload.platform == Platform.QIANCHENG:
                haystack = str(job_payload.get("title", "") or "").lower()
            else:
                haystack = " ".join(
                    [
                        job_payload.get("title", ""),
                        job_payload.get("description", ""),
                        job_payload.get("company_name", ""),
                        " ".join(job_payload.get("tags", [])),
                    ]
                ).lower()
            if not any(keyword.lower() in haystack for keyword in payload.keywords):
                return {"matched": False, "reason": "未命中关键词筛选"}

        salary_result = self._match_salary_range(job_payload, payload)
        if salary_result is not None:
            return salary_result

        if payload.platform == Platform.BOSS and payload.boss_active_statuses and "不限" not in payload.boss_active_statuses:
            recruiter_status = self._normalize_boss_activity_status(job_payload.get("recruiter_status", ""))
            if not recruiter_status:
                return {"matched": False, "reason": "未识别到招聘者活跃状态"}
            if recruiter_status not in payload.boss_active_statuses:
                return {"matched": False, "reason": f"活跃度不匹配: {recruiter_status}"}

        return {"matched": True, "reason": "命中过滤条件"}

    def _match_company_blacklist(
        self, job_payload: dict, payload: StartTaskRequest
    ) -> dict[str, str | bool] | None:
        if not payload.enable_company_blacklist:
            return None

        company_name = " ".join(str(job_payload.get("company_name", "") or "").strip().split())
        if not company_name:
            return None

        normalized_company_name = company_name.lower()
        for blacklist_item in self._normalize_text_filters(payload.company_blacklist):
            if blacklist_item.lower() in normalized_company_name:
                return {
                    "matched": False,
                    "reason": f"公司黑名单命中: {company_name}（关键词: {blacklist_item}）",
                }
        return None

    def _match_job_title_blacklist(
        self, job_payload: dict, payload: StartTaskRequest
    ) -> dict[str, str | bool] | None:
        if not payload.enable_job_title_blacklist:
            return None

        raw_title = str(job_payload.get("title", "") or "").strip()
        if not raw_title:
            return None
        normalized_title = " ".join(raw_title.split()).lower()
        for blacklist_item in self._normalize_text_filters(payload.job_title_blacklist):
            if blacklist_item.lower() in normalized_title:
                return {
                    "matched": False,
                    "reason": f"岗位标题黑名单命中: {raw_title}（关键词: {blacklist_item}）",
                }
        return None

    def _match_salary_range(self, job_payload: dict, payload: StartTaskRequest) -> dict[str, str | bool] | None:
        expected_min = payload.salary_min
        expected_max = payload.salary_max
        if expected_min is None and expected_max is None:
            return None

        raw_salary_text = str(job_payload.get("salary_text", "") or "").strip()
        job_min = job_payload.get("salary_min")
        job_max = job_payload.get("salary_max")

        if job_min is None and job_max is None and raw_salary_text:
            try:
                job_min, job_max = self._normalize_salary_by_platform(payload.platform, raw_salary_text)
            except Exception:  # noqa: BLE001
                logger.warning("Salary normalize failed during filter matching: salary_text=%s", raw_salary_text, exc_info=True)

        is_daily_salary = "天" in raw_salary_text and "元" in raw_salary_text
        compare_min = self._convert_expected_salary_bound(expected_min, is_daily_salary)
        compare_max = self._convert_expected_salary_bound(expected_max, is_daily_salary)

        if job_min is None and job_max is None:
            return {"matched": False, "reason": f"薪资无法识别: {raw_salary_text or '未标注'}"}

        # 上下限都按硬条件处理，避免 5-20K 仍投递 25K+ 岗位。
        if compare_min is not None and job_min is not None and job_min < compare_min:
            if payload.salary_floor_flexible_enabled and payload.salary_floor_required_max is not None:
                floor_required_max = self._convert_expected_salary_bound(payload.salary_floor_required_max, is_daily_salary)
                if floor_required_max is not None and job_max is not None and job_max >= floor_required_max:
                    return None
            return {
                "matched": False,
                "reason": f"薪资下限过低: {raw_salary_text or f'{job_min:g}'} < {self._format_expected_salary(compare_min, is_daily_salary)}",
            }

        if compare_max is not None and job_max is not None and job_max > compare_max:
            return {
                "matched": False,
                "reason": f"薪资上限过高: {raw_salary_text or f'{job_max:g}'} > {self._format_expected_salary(compare_max, is_daily_salary)}",
            }

        return None

    @staticmethod
    def _convert_expected_salary_bound(value: int | None, is_daily_salary: bool) -> float | None:
        if value is None:
            return None
        if not is_daily_salary:
            return float(value)
        return round((float(value) * 1000) / 21.75, 2)

    @staticmethod
    def _format_expected_salary(value: float, is_daily_salary: bool) -> str:
        if is_daily_salary:
            return f"{value:g}元/天"
        return f"{value:g}K"

    @staticmethod
    def _normalize_salary_by_platform(platform: Platform, raw_salary_text: str) -> tuple[float | None, float | None]:
        if platform == Platform.ZHILIAN:
            return ZhilianAdapter.normalize_salary(raw_salary_text)
        if platform == Platform.QIANCHENG:
            return QianchengAdapter.normalize_salary(raw_salary_text)
        if platform == Platform.LIEPIN:
            return LiepinAdapter.normalize_salary(raw_salary_text)
        if platform == Platform.YUPAO:
            return YupaoAdapter.normalize_salary(raw_salary_text)
        return BossAdapter.normalize_salary(raw_salary_text)

    def _normalize_boss_activity_status(self, raw_status: str) -> str:
        status = (raw_status or "").strip()
        if not status:
            return ""
        if status == "在线" or "在线" in status:
            return "在线"
        if "刚刚" in status:
            return "刚刚活跃"
        if "今日" in status:
            return "今日活跃"
        if "3日" in status or "三日" in status:
            return "3日内活跃"
        if "本周" in status:
            return "本周活跃"
        if "本月" in status:
            return "本月活跃"
        if "半年" in status:
            return "半年前活跃"
        minute_match = any(token in status for token in ("分钟前", "分钟内", "秒前", "刚刚"))
        if minute_match:
            return "刚刚活跃"
        if any(token in status for token in ("小时前", "今天")):
            return "今日活跃"
        if any(token in status for token in ("昨天", "前天", "天前")):
            return "3日内活跃"
        if any(token in status for token in ("周前",)):
            return "本周活跃"
        if any(token in status for token in ("月前",)):
            return "本月活跃"
        return status


    @staticmethod
    def _derive_auto_send_chat(payload: StartTaskRequest) -> bool:
        if not TaskRunner._platform_supports_chat(payload.platform):
            return False
        if payload.platform == Platform.BOSS:
            return bool(
                payload.use_chat_template
                or payload.enable_common_phrases
                or payload.enable_image_resume
                or payload.auto_agree_wechat_card
                or payload.enable_follow_up_reply
            )
        return bool(payload.auto_send_chat)

    def _initial_chat_send_status(self, payload: StartTaskRequest, job_payload: dict, delivery_status: str) -> str:
        if delivery_status != DeliveryStatus.DELIVERED.value:
            return ChatSendStatus.SKIPPED.value
        existing_send_status = str(job_payload.get("send_status", "") or "").strip()
        if existing_send_status:
            return existing_send_status
        if not self._derive_auto_send_chat(payload):
            return ChatSendStatus.SKIPPED.value
        if bool(job_payload.get("chat_sent", False)):
            return ChatSendStatus.SENT.value
        return ChatSendStatus.PENDING.value

    @staticmethod
    def _normalize_chat_match_key(company_name: str, job_title: str, hr_name: str) -> str:
        parts = [company_name or "", job_title or "", hr_name or ""]
        normalized = [" ".join(part.strip().lower().split()) for part in parts]
        if not any(normalized):
            return ""
        return " | ".join(normalized)


    def _find_chat_delivery(
        self,
        db,
        task_id: int,
        platform_value: str,
        *,
        platform_job_id: str = "",
        job_title: str = "",
        company_name: str = "",
        hr_name: str = "",
        weak_match_key: str = "",
    ) -> DeliveryRecord | None:
        base_filters = [
            DeliveryRecord.task_run_id == task_id,
            DeliveryRecord.platform == platform_value,
        ]

        # Strategy 1: exact platform_job_id match, prefer PENDING send_status
        if platform_job_id:
            stmt = (
                select(DeliveryRecord)
                .join(Job, DeliveryRecord.job_id == Job.id)
                .where(*base_filters, Job.platform_job_id == platform_job_id)
                .order_by(desc(DeliveryRecord.id))
            )
            rows = list(db.execute(stmt))
            if rows:
                for row in rows:
                    delivery = row[0]
                    if delivery.send_status in (ChatSendStatus.PENDING.value, ChatSendStatus.SKIPPED.value):
                        return delivery
                return rows[0][0]

        # Strategy 2: company_name contains match (case-insensitive, bidirectional)
        normalized_company = " ".join((company_name or "").strip().lower().split())
        normalized_title = " ".join((job_title or "").strip().lower().split())
        if normalized_company or normalized_title:
            stmt = (
                select(DeliveryRecord, Job)
                .join(Job, DeliveryRecord.job_id == Job.id)
                .where(*base_filters)
                .order_by(desc(DeliveryRecord.id))
            )
            pending_candidates: list = []
            other_candidates: list = []
            for delivery, job in db.execute(stmt):
                existing_company = " ".join((job.company_name or "").strip().lower().split())
                existing_title = " ".join((job.title or "").strip().lower().split())
                matched = False
                if normalized_company and existing_company:
                    if normalized_company in existing_company or existing_company in normalized_company:
                        matched = True
                if not matched and normalized_title and existing_title:
                    if normalized_title in existing_title or existing_title in normalized_title:
                        matched = True
                if not matched:
                    continue
                if delivery.send_status in (ChatSendStatus.PENDING.value, ChatSendStatus.SKIPPED.value):
                    pending_candidates.append(delivery)
                else:
                    other_candidates.append(delivery)
            if pending_candidates:
                return pending_candidates[0]
            if other_candidates:
                return other_candidates[0]

        # Strategy 3: weak_match_key fallback (exact normalized 3-field match)
        if weak_match_key:
            stmt = (
                select(DeliveryRecord, Job)
                .join(Job, DeliveryRecord.job_id == Job.id)
                .where(*base_filters)
                .order_by(desc(DeliveryRecord.id))
            )
            pending_candidates: list = []
            other_candidates: list = []
            for candidate_delivery, candidate_job in db.execute(stmt):
                candidate_key = self._normalize_chat_match_key(
                    candidate_job.company_name,
                    candidate_job.title,
                    candidate_delivery.chat_hr_name or hr_name,
                )
                if candidate_key and candidate_key == weak_match_key:
                    if candidate_delivery.send_status in (ChatSendStatus.PENDING.value, ChatSendStatus.SKIPPED.value):
                        pending_candidates.append(candidate_delivery)
                    else:
                        other_candidates.append(candidate_delivery)
            if pending_candidates:
                return pending_candidates[0]
            if other_candidates:
                return other_candidates[0]

        return None

    def _match_cross_account_dedupe(self, job_payload: dict, payload: StartTaskRequest) -> dict[str, str | bool] | None:
        if not payload.cross_account_dedupe_enabled or not payload.account_id:
            return None

        compare_title = (job_payload.get("title") or "").strip()
        compare_company = (job_payload.get("company_name") or "").strip()
        threshold = local_now() - timedelta(days=payload.cooldown_days)

        with SessionLocal() as db:
            stmt = (
                select(DeliveryRecord, Job)
                .join(Job, DeliveryRecord.job_id == Job.id)
                .where(
                    DeliveryRecord.platform == payload.platform.value,
                    DeliveryRecord.status == DeliveryStatus.DELIVERED.value,
                    DeliveryRecord.account_id.is_not(None),
                    DeliveryRecord.created_at >= threshold,
                )
                .order_by(desc(DeliveryRecord.created_at))
            )
            for delivery, job in db.execute(stmt):
                account_scope = (
                    "当前账号"
                    if delivery.account_id == payload.account_id
                    else f"账号 {delivery.account_id}"
                )
                if (
                    payload.dedupe_match_by_title
                    and compare_title
                    and compare_company
                    and job.title == compare_title
                    and job.company_name == compare_company
                ):
                    return {
                        "matched": False,
                        "reason": f"历史投递去重命中：{account_scope} 已投递同标题同公司岗位",
                    }
                if payload.dedupe_match_by_company and compare_company and job.company_name == compare_company:
                    return {
                        "matched": False,
                        "reason": f"历史投递去重命中：{account_scope} 已投递该公司岗位",
                    }
        return None

    def _resolve_account(self, db, payload: StartTaskRequest) -> Account:
        account = get_account(db, payload.account_id) if payload.account_id else None
        if account is None:
            account = db.scalar(
                select(Account).where(Account.platform == payload.platform.value, Account.is_default.is_(True)).limit(1)
            )
        if account is None:
            raise RuntimeError(f"{payload.platform.value} 平台暂无可用账号，请先创建账号。")
        if account.platform != payload.platform.value:
            raise RuntimeError("所选账号与当前平台不匹配。")
        if not account.is_active:
            raise RuntimeError("所选账号已停用。")
        payload.account_id = account.id
        return account

    @staticmethod
    def _assign_current_step(task: TaskRun, current_step: str, *, force_timestamp: bool = False) -> None:
        if force_timestamp or task.current_step != current_step or task.current_step_started_at is None:
            task.current_step_started_at = local_now()
        task.current_step = current_step

    def _set_status(self, task_run_id: int, status: TaskStatus, current_step: str) -> None:
        platform_key: str | None = None
        with SessionLocal() as db:
            task = db.get(TaskRun, task_run_id)
            if not task:
                return
            platform_key = task.platform
            task.status = status.value
            self._assign_current_step(task, current_step)
            if status in {TaskStatus.STOPPED, TaskStatus.COMPLETED, TaskStatus.FAILED}:
                task.finished_at = local_now()
            db.commit()
        if platform_key:
            live_preview_service.update_status(
                platform_key,
                task_run_id=task_run_id,
                current_step=current_step,
                active=status in {TaskStatus.RUNNING, TaskStatus.STOPPING},
                message=current_step,
            )

    def _log(self, platform: Platform, task_run_id: int, event_type: str, message: str, level: str = "INFO") -> None:
        with SessionLocal() as db:
            db.add(
                TaskLog(
                    platform=platform.value,
                    task_run_id=task_run_id,
                    level=level,
                    event_type=event_type,
                    message=message,
                )
            )
            db.commit()


task_runner = TaskRunner()
