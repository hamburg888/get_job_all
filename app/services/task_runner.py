import logging
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
import sys

from sqlalchemy import desc, select

from app.automation.adapters.zhilian_adapter import ZhilianAdapter
from app.core.config import settings
from app.core.time_utils import local_now
from app.db.database import SessionLocal
from app.models.entities import Account, DeliveryRecord, DeliveryStatus, Job, Platform, RuleConfig, TaskLog, TaskRun, TaskStatus
from app.schemas.common import StartTaskRequest, TaskStatusResponse
from app.services.account_service import get_account

logger = logging.getLogger(__name__)


@dataclass
class WorkerState:
    thread: threading.Thread
    stop_event: threading.Event
    login_confirm_event: threading.Event
    task_run_id: int


class TaskRunner:
    def __init__(self) -> None:
        self._workers: dict[str, WorkerState] = {}
        self._lock = threading.Lock()

    def start(self, payload: StartTaskRequest) -> TaskStatusResponse:
        platform_key = payload.platform.value
        with self._lock:
            existing = self._workers.get(platform_key)
            if existing and existing.thread.is_alive():
                return self.status(payload.platform)

            with SessionLocal() as db:
                account = self._resolve_account(db, payload)
                auto_send_chat = payload.auto_send_chat if payload.platform == Platform.BOSS else False
                chat_template = payload.chat_template if payload.platform == Platform.BOSS else ""
                rule = db.scalar(select(RuleConfig).where(RuleConfig.platform == platform_key).limit(1))
                if rule:
                    rule.keyword = ",".join(payload.keywords or payload.search_terms)
                    rule.city = ",".join(payload.cities)
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
                    keyword=",".join(payload.search_terms),
                    status=TaskStatus.RUNNING.value,
                    current_step="正在准备浏览器与筛选条件",
                    processed_count=0,
                    delivered_count=0,
                    failed_count=0,
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
                db.commit()

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
                self._set_status(worker.task_run_id, TaskStatus.STOPPING, "收到停止指令，正在收尾")
        return self.status(platform)

    def status(self, platform: Platform) -> TaskStatusResponse:
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
            waiting_for_login = "登录" in task.current_step and "确认" in task.current_step
            return TaskStatusResponse(
                platform=platform,
                status=TaskStatus(task.status),
                current_step=task.current_step,
                processed_count=task.processed_count,
                delivered_count=task.delivered_count,
                failed_count=task.failed_count,
                started_at=task.started_at,
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
            else:
                self._run_mock_boss(task_run_id, payload, stop_event)
            final_status = TaskStatus.STOPPED if stop_event.is_set() else TaskStatus.COMPLETED
            final_step = "任务已停止" if stop_event.is_set() else "任务执行完成"
            self._set_status(task_run_id, final_status, final_step)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Task failed: %s", exc)
            self._log(payload.platform, task_run_id, "task_error", f"任务异常中断: {exc}", level="ERROR")
            self._set_status(task_run_id, TaskStatus.FAILED, f"任务执行失败: {exc}")
        finally:
            with self._lock:
                self._workers.pop(payload.platform.value, None)

    def _run_zhilian(self, task_run_id: int, payload: StartTaskRequest, stop_event: threading.Event) -> None:
        adapter = ZhilianAdapter()
        with SessionLocal() as db:
            account = self._resolve_account(db, payload)

        def update_step(message: str) -> None:
            with SessionLocal() as db:
                task = db.get(TaskRun, task_run_id)
                if not task:
                    return
                task.current_step = message
                db.add(
                    TaskLog(
                        platform=payload.platform.value,
                        task_run_id=task.id,
                        event_type="task_progress",
                        message=message,
                    )
                )
                db.commit()

        def wait_for_login_confirmation() -> bool:
            while not stop_event.is_set():
                worker_state = self._workers.get(payload.platform.value)
                if worker_state and worker_state.login_confirm_event.is_set():
                    worker_state.login_confirm_event.clear()
                    return True
                time.sleep(0.5)
            return False

        def process_job(job_payload: dict) -> None:
            if stop_event.is_set():
                return
            with SessionLocal() as db:
                task = db.get(TaskRun, task_run_id)
                if not task:
                    return
                task.current_step = f"处理中: {job_payload['title']}"
                task.processed_count += 1

                filter_result = self._match_filters(job_payload, payload)
                delivery_status = (
                    DeliveryStatus.DELIVERED.value
                    if filter_result["matched"] and job_payload.get("delivery_success", True)
                    else DeliveryStatus.SKIPPED.value if not filter_result["matched"] else DeliveryStatus.FAILED.value
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
                    existing_job.salary_text = job_payload["salary_text"]
                    existing_job.salary_min = job_payload.get("salary_min")
                    existing_job.salary_max = job_payload.get("salary_max")
                    existing_job.city = job_payload.get("city", payload.cities[0] if payload.cities else "")
                    existing_job.experience_text = job_payload.get("experience_text", "不限")
                    existing_job.description = job_payload["description"]
                    existing_job.detail_url = job_payload["detail_url"]
                    existing_job.tags = job_payload.get("tags", [])
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
                    chat_sent=False,
                    chat_message="",
                )
                db.add(delivery)
                if not filter_result["matched"]:
                    task.failed_count += 1
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
                            f"搜索词 {job_payload.get('search_term', '')} | "
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
                    )
                )
        else:
            self._set_status(task_run_id, TaskStatus.RUNNING, "模拟执行智联搜索与投递流程")
            adapter.run_sync_mock(",".join(payload.search_terms), process_job, stop_event.is_set)

    def _run_mock_boss(self, task_run_id: int, payload: StartTaskRequest, stop_event: threading.Event) -> None:
        samples = [
            ("Python后端开发", "云岚科技", "20-30K", "3-5年", "已投递并进入聊天页，问候语发送成功", True),
            ("高级平台工程师", "海石智能", "30-45K", "5-10年", "岗位命中黑名单关键词，已跳过", False),
        ]
        for index, sample in enumerate(samples, start=1):
            if stop_event.is_set():
                break
            title, company, salary, exp, reason, sent_chat = sample
            with SessionLocal() as db:
                task = db.get(TaskRun, task_run_id)
                if not task:
                    return
                task.current_step = f"Boss 聊天页联动中: {title}"
                task.processed_count += 1
                job = Job(
                    platform=payload.platform.value,
                    platform_job_id=f"boss-runtime-{task_run_id}-{index}",
                    title=title,
                    company_name=company,
                    salary_text=salary,
                    salary_min=20 if index == 1 else 30,
                    salary_max=30 if index == 1 else 45,
                    city=(payload.cities[0] if payload.cities else "深圳"),
                    experience_text=exp,
                    description="Boss 双页面任务演示数据",
                    detail_url=f"https://www.zhipin.com/job_detail/runtime-{task_run_id}-{index}.html",
                    tags=["Boss", "Chat", "Playwright"],
                    latest_status=DeliveryStatus.DELIVERED.value if sent_chat else DeliveryStatus.SKIPPED.value,
                    task_run_id=task.id,
                )
                db.add(job)
                db.flush()
                delivery = DeliveryRecord(
                    job_id=job.id,
                    task_run_id=task.id,
                    account_id=task.account_id,
                    platform=payload.platform.value,
                    status=DeliveryStatus.DELIVERED.value if sent_chat else DeliveryStatus.SKIPPED.value,
                    status_reason=reason,
                    chat_sent=sent_chat,
                    chat_message=payload.chat_template.format(job_title=title, company_name=company) if sent_chat else "",
                )
                db.add(delivery)
                if sent_chat:
                    task.delivered_count += 1
                self._log(payload.platform, task.id, "chat_sync", f"Boss 新会话已处理: {company} / {title}")
                db.commit()
            time.sleep(1.5)

    def _match_filters(self, job_payload: dict, payload: StartTaskRequest) -> dict[str, str | bool]:
        shared_result = self._match_cross_account_dedupe(job_payload, payload)
        if shared_result is not None:
            return shared_result

        if payload.cities:
            job_city = (job_payload.get("city") or "").strip()
            if job_city and job_city not in payload.cities:
                return {"matched": False, "reason": f"城市不匹配: {job_city}"}

        if payload.keywords:
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

        return {"matched": True, "reason": "命中过滤条件"}

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
                    DeliveryRecord.account_id != payload.account_id,
                    DeliveryRecord.created_at >= threshold,
                )
                .order_by(desc(DeliveryRecord.created_at))
            )
            for delivery, job in db.execute(stmt):
                if payload.dedupe_match_by_title and compare_title and job.title == compare_title:
                    return {
                        "matched": False,
                        "reason": f"共享去重命中：账号 {delivery.account_id} 已投递同名岗位",
                    }
                if payload.dedupe_match_by_company and compare_company and job.company_name == compare_company:
                    return {
                        "matched": False,
                        "reason": f"共享去重命中：账号 {delivery.account_id} 已投递同公司岗位",
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

    def _set_status(self, task_run_id: int, status: TaskStatus, current_step: str) -> None:
        with SessionLocal() as db:
            task = db.get(TaskRun, task_run_id)
            if not task:
                return
            task.status = status.value
            task.current_step = current_step
            if status in {TaskStatus.STOPPED, TaskStatus.COMPLETED, TaskStatus.FAILED}:
                task.finished_at = local_now()
            db.commit()

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
