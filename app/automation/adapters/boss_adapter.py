import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from app.automation.browser_factory import (
    LAODENG_INIT_SCRIPT,
    STEALTH_INIT_SCRIPT,
    build_browser_launch_options,
)
from app.automation.async_runner import run_async_blocking
from app.core.config import settings
from app.services.live_preview_service import live_preview_service

logger = logging.getLogger(__name__)

BOSS_SALARY_FONT_MAP = str.maketrans(
    {
        ord("\uE030"): "0",
        ord("\uE031"): "1",
        ord("\uE032"): "2",
        ord("\uE033"): "3",
        ord("\uE034"): "4",
        ord("\uE035"): "5",
        ord("\uE036"): "6",
        ord("\uE037"): "7",
        ord("\uE038"): "8",
        ord("\uE039"): "9",
        ord("\uE03A"): "10",
        ord("\uE8F0"): "0",
        ord("\uE8F1"): "1",
        ord("\uE8F2"): "2",
        ord("\uE8F3"): "3",
        ord("\uE8F4"): "4",
        ord("\uE8F5"): "5",
        ord("\uE8F6"): "6",
        ord("\uE8F7"): "7",
        ord("\uE8F8"): "8",
        ord("\uE8F9"): "9",
    }
)


@dataclass(slots=True)
class BossSelectors:
    job_card: str = "li.job-card-box"
    job_name: str = ".job-name"
    job_address_primary: str = ".job-address-desc"
    job_address_secondary: str = ".company-location"
    job_address_tertiary: str = ".job-area"
    headhunter_tag: str = ".job-tag-icon"
    detail_job_name_primary: str = ".job-detail-info .job-name"
    detail_job_name_secondary: str = ".position-name"
    detail_job_name_tertiary: str = '[class*="position-content"] .left-content .position-name'
    detail_company_attr: str = ".boss-info-attr"
    detail_salary_candidates: tuple[str, ...] = (
        ".job-detail-info .job-salary",
        ".job-detail-info .salary",
        '[class*="position-content"] .job-salary',
        ".salary",
        ".job-salary",
        '[class*="salary"]',
        '[class*="red"]',
    )
    detail_experience_candidates: tuple[str, ...] = (".job-limit", ".job-experience", '[class*="experience"]')
    detail_description_candidates: tuple[str, ...] = (
        ".job-detail-body p.desc",
        ".job-detail-body .desc",
        ".job-detail-body .job-description",
        ".job-sec-text",
        ".job-detail-section",
        ".job-description",
        ".text",
    )
    detail_tags_candidates: tuple[str, ...] = (
        ".job-detail-body .job-label-list li",
        ".job-detail-body .job-label-list span",
        ".job-label-list li",
        ".job-tags span",
        ".tag-list span",
        '[class*="tag"] span',
    )
    chat_button: str = "a.op-btn-chat"
    stay_on_page_button: str = ".default-btn.cancel-btn"
    greet_boss_dialog: str = ".greet-boss-dialog"
    greet_boss_layer: str = ".greet-boss-layer"
    recruiter_online_tag: str = ".boss-online-tag"
    recruiter_active_time: str = ".boss-active-time"
    conversation_items: tuple[str, ...] = (
        'ul[role="group"] li[role="listitem"]:has(.friend-content-warp)',
        ".user-list-content li",
        ".chat-conversation li",
    )
    conversation_figure: tuple[str, ...] = (".figure", ".friend-content-warp", ".name-box")
    conversation_name: str = ".name-text"
    conversation_company: str = ".name-box span:nth-child(2)"
    friend_message_items: str = "li.message-item.item-friend"
    friend_message_text: str = ".text span"
    card_wrap: str = ".message-card-wrap"
    card_button: str = ".card-btn"
    input_box: str = "#chat-input"
    send_button: str = ".btn-send"
    common_phrase_button: tuple[str, ...] = (".btn-dict", '.toolbar-btn:has-text("常用语")')
    common_phrase_panels: tuple[str, ...] = (
        ".panel-resume.sentence-popover",
        ".sentence-popover",
        ".dict-popover",
        ".dict-panel",
        ".dict-list",
        ".boss-popup__wrapper",
        ".common-words-panel",
        'ul[data-v-f115c50c=""]',
        '[class*="dict"]',
        '[class*="sentence"]',
    )
    common_phrase_items: tuple[str, ...] = (
        ".dict-list li",
        ".boss-popup__wrapper li",
        "ul > li",
        "li",
        ".dict-item",
        ".sentence-item",
        ".list-item",
        '[role="option"]',
        "button",
        "a",
    )
    message_nav_link: tuple[str, ...] = ('a[ka="header-message"]', 'a[href*="/web/geek/chat"]')
    header_login_button: str = "a.header-login-btn"
    header_user_name: str = ".user-nav .nav-figure .label-text"
    header_user_link: str = '.user-nav a[ka="header-username"]'
    header_logout_link: str = ".user-nav .link-logout"
    login_text_candidates: tuple[str, ...] = ("登录", "登录/注册", "立即登录")


@dataclass(slots=True)
class BossConversationState:
    hr_key: str
    hr_name: str = ""
    company_name: str = ""
    last_row_text: str = ""
    last_friend_message: str = ""
    greeting_sent: bool = False
    image_sent: bool = False
    processing: bool = False
    suppress_until: float = 0.0
    greeted_job_ids: set[str] = field(default_factory=set)
    replied_messages: set[str] = field(default_factory=set)


@dataclass(slots=True)
class BossChatTask:
    kind: str
    dedupe_key: str
    conversation_key: str = ""
    hr_name: str = ""
    company_name: str = ""
    job_title: str = ""
    platform_job_id: str = ""
    detail_url: str = ""
    source_message: str = ""
    job_payload: dict = field(default_factory=dict)


class BossAdapter:
    def __init__(self) -> None:
        self.selectors = BossSelectors()
        self._runtime_lock = threading.Lock()
        self._active_loop = None
        self._active_context = None
        self._active_browser = None
        self._active_chrome_pid: int | None = None

    async def run_hybrid_flow(
        self,
        profile_dir: str,
        *,
        jobs_entry_url: str | None,
        search_terms: list[str],
        keywords: list[str],
        cities: list[str],
        auto_send_chat: bool,
        use_chat_template: bool,
        chat_message_template: str,
        enable_common_phrases: bool,
        common_phrase_indexes: list[int],
        enable_image_resume: bool,
        image_resume_path: str,
        auto_agree_wechat_card: bool,
        boss_first_greeting_only: bool,
        enable_follow_up_reply: bool,
        follow_up_reply_rules: list[dict],
        delivery_interval_seconds: int,
        max_deliveries_per_run: int,
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None = None,
        job_callback: Callable[[dict], None] | None = None,
        job_precheck_callback: Callable[[dict], dict[str, str | bool]] | None = None,
        chat_event_callback: Callable[[dict], None] | None = None,
        confirm_login_callback: Callable[[], bool] | None = None,
        task_run_id: int | None = None,
        headless: bool = False,
        live_preview_enabled: bool = False,
        live_preview_interval_ms: int = 1000,
    ) -> None:
        from playwright.async_api import async_playwright

        jobs_url = (jobs_entry_url or settings.boss_jobs_url).strip() or settings.boss_jobs_url
        chat_url = settings.boss_chat_url
        include_terms = self._normalize_terms(search_terms + keywords)
        city_terms = self._normalize_terms(cities)
        max_successful_deliveries = max(1, max_deliveries_per_run)
        successful_deliveries = 0
        delivery_interval_ms = max(0, delivery_interval_seconds) * 1000
        launch_mode = "fake_headless" if headless and settings.browser_fake_headless else "headless" if headless else "visible"
        use_persistent_context = bool(headless and not settings.browser_fake_headless)
        logger.info(
            "Boss launch mode resolved: mode=%s requested_headless=%s settings_headless=%s fake_headless=%s",
            launch_mode,
            headless,
            settings.browser_headless,
            settings.browser_fake_headless,
        )

        async with async_playwright() as playwright:
            browser = None
            chrome_pid: int | None = None
            if use_persistent_context:
                launch_options = build_browser_launch_options(headless)
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    **launch_options,
                )
            else:
                chrome_path = self._resolve_chrome_executable()
                profile_path = Path(profile_dir)
                if not profile_path.is_absolute():
                    profile_path = Path.cwd() / profile_path
                profile_path.mkdir(parents=True, exist_ok=True)

                debug_port = self._pick_free_port()
                chrome_pid = self._launch_external_chrome(
                    chrome_path,
                    profile_path,
                    debug_port,
                    jobs_url,
                    launch_options=build_browser_launch_options(headless),
                )
                self._bind_runtime(asyncio.get_running_loop(), None, None, chrome_pid)
                ws_endpoint = self._wait_for_cdp_endpoint(debug_port, timeout=20, should_stop=should_stop)
                if not ws_endpoint:
                    raise RuntimeError("Boss 外部 Chrome 已启动，但未能连接到调试端口。")
                browser = await playwright.chromium.connect_over_cdp(ws_endpoint)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()

            self._bind_runtime(asyncio.get_running_loop(), context, browser, chrome_pid)
            await context.add_init_script(STEALTH_INIT_SCRIPT)
            await context.add_init_script(LAODENG_INIT_SCRIPT)
            jobs_page, chat_page = await self._ensure_pages(context, jobs_url, chat_url)

            preview_ref = {"page": jobs_page}
            preview_task = None
            if headless and live_preview_enabled and task_run_id is not None:
                preview_task = asyncio.create_task(
                    self._preview_loop(context, preview_ref, should_stop, task_run_id, live_preview_interval_ms)
                )

            conversation_states: dict[str, BossConversationState] = {}
            pending_chat_jobs: asyncio.Queue[dict] = asyncio.Queue()
            delivery_finished = asyncio.Event()
            chat_task: asyncio.Task | None = None

            try:
                if await self.is_login_required(jobs_page, chat_page):
                    if status_callback:
                        status_callback("Boss 直聘当前未登录，请先在浏览器中完成登录，然后回到前端点击确认已登录。")
                    await self.wait_for_login(
                        jobs_page,
                        chat_page,
                        should_stop,
                        status_callback=status_callback,
                        confirm_login_callback=confirm_login_callback,
                    )
                    if should_stop():
                        return

                async def delivery_worker() -> None:
                    if status_callback:
                        status_callback("Boss 岗位列表已打开，正在滚动加载岗位")
                    preview_ref["page"] = jobs_page
                    await self._auto_scroll_job_list(jobs_page, should_stop)

                    cards = await self._collect_job_cards(jobs_page, include_terms, city_terms)
                    if not cards:
                        if status_callback:
                            status_callback("Boss 当前列表没有命中的岗位")
                    else:
                        nonlocal successful_deliveries
                        for card in cards:
                            if should_stop():
                                break

                            if status_callback:
                                status_callback(f"Boss 正在处理岗位: {card.get('title') or '未知岗位'}")

                            try:
                                preview_ref["page"] = jobs_page
                                job_payload = await self._open_job_and_extract(jobs_page, card)
                                job_payload["search_term"] = "、".join(search_terms) if search_terms else "当前列表"
                                job_payload["matched"] = True
                                job_payload["status_reason"] = "命中过滤条件"
                                job_payload["delivery_success"] = False
                                job_payload["chat_sent"] = False
                                job_payload["chat_message"] = ""

                                if job_precheck_callback:
                                    precheck = job_precheck_callback(job_payload)
                                    job_payload["matched"] = bool(precheck.get("matched", True))
                                    job_payload["status_reason"] = str(precheck.get("reason", "命中过滤条件"))

                                if not job_payload["matched"]:
                                    if job_callback:
                                        job_callback(job_payload)
                                    await jobs_page.wait_for_timeout(800)
                                    continue

                                click_result = await self._start_chat_from_job(jobs_page)
                                job_payload["delivery_success"] = click_result["success"]
                                job_payload["status_reason"] = click_result["reason"]

                                if click_result["success"] and auto_send_chat:
                                    await pending_chat_jobs.put(dict(job_payload))
                                    if status_callback:
                                        status_callback(f"Boss 已发起沟通并加入聊天队列: {job_payload['title']}")
                                elif click_result["success"] and status_callback:
                                    status_callback(f"Boss 已发起沟通: {job_payload['title']}，未开启自动聊天")

                                if job_callback:
                                    job_callback(job_payload)

                                if click_result["success"]:
                                    successful_deliveries += 1
                                    if successful_deliveries >= max_successful_deliveries:
                                        if status_callback:
                                            status_callback(f"Boss 已达到单次投递成功上限 {max_successful_deliveries}，停止继续投递")
                                        break
                                    if delivery_interval_ms > 0:
                                        if status_callback:
                                            status_callback(f"Boss 等待 {delivery_interval_seconds} 秒后继续下一次投递")
                                        await jobs_page.wait_for_timeout(delivery_interval_ms)
                            except Exception as exc:  # noqa: BLE001
                                logger.warning(
                                    "Boss failed to process job card: title=%s index=%s",
                                    card.get("title", ""),
                                    card.get("index"),
                                    exc_info=True,
                                )
                                if status_callback:
                                    status_callback(
                                        f"Boss 跳过当前岗位（页面弹层或点击异常）: {card.get('title') or '未知岗位'} / {exc}"
                                    )
                                await self._dismiss_greet_boss_dialog(jobs_page, max_rounds=6)
                                await jobs_page.wait_for_timeout(1000)
                                continue

                            preview_ref["page"] = jobs_page
                            await jobs_page.wait_for_timeout(1200)

                async def chat_worker() -> None:
                    preview_ref["page"] = chat_page
                    await self._prime_chat_conversations(chat_page, conversation_states)
                    if status_callback:
                        status_callback("Boss 自动聊天协程已启动，将持续监听新会话和新消息")
                    await self._monitor_chat_messages(
                        chat_page,
                        conversation_states,
                        use_chat_template,
                        chat_message_template,
                        enable_common_phrases,
                        common_phrase_indexes,
                        enable_image_resume,
                        image_resume_path,
                        auto_agree_wechat_card,
                        boss_first_greeting_only,
                        enable_follow_up_reply,
                        follow_up_reply_rules,
                        should_stop,
                        status_callback,
                        chat_event_callback,
                        pending_job_queue=pending_chat_jobs,
                        delivery_finished=delivery_finished,
                    )

                if auto_send_chat:
                    chat_task = asyncio.create_task(chat_worker())
                await delivery_worker()
                delivery_finished.set()

                if chat_task is not None and not should_stop():
                    if status_callback:
                        status_callback("Boss 投递协程已结束，自动聊天协程继续监听 5 分钟新消息")
                    await chat_task
                elif status_callback and auto_send_chat:
                    status_callback("Boss 自动聊天协程已停止")
            finally:
                delivery_finished.set()
                if chat_task:
                    chat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await chat_task
                if preview_task:
                    preview_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await preview_task
                with contextlib.suppress(Exception):
                    await context.close()
                if browser is not None:
                    with contextlib.suppress(Exception):
                        await browser.close()
                self._clear_runtime()
                self._terminate_chrome_process(chrome_pid)

    def request_stop(self) -> None:
        with self._runtime_lock:
            loop = self._active_loop
            context = self._active_context
            browser = self._active_browser
            chrome_pid = self._active_chrome_pid
            self._active_chrome_pid = None

        if chrome_pid:
            self._terminate_chrome_process(chrome_pid)

        if loop is None:
            return

        def _interrupt_runtime() -> None:
            if context is not None:
                asyncio.create_task(self._safe_close_context(context))
            elif browser is not None:
                asyncio.create_task(self._safe_close_browser(browser))

        with contextlib.suppress(Exception):
            loop.call_soon_threadsafe(_interrupt_runtime)

    def _bind_runtime(self, loop, context, browser, chrome_pid: int | None) -> None:
        with self._runtime_lock:
            self._active_loop = loop
            self._active_context = context
            self._active_browser = browser
            self._active_chrome_pid = chrome_pid

    def _clear_runtime(self) -> None:
        with self._runtime_lock:
            self._active_loop = None
            self._active_context = None
            self._active_browser = None
            self._active_chrome_pid = None

    async def _safe_close_context(self, context) -> None:
        with contextlib.suppress(Exception):
            await context.close()

    async def _safe_close_browser(self, browser) -> None:
        with contextlib.suppress(Exception):
            await browser.close()

    async def _prime_chat_conversations(
        self,
        chat_page,
        conversation_states: dict[str, BossConversationState],
    ) -> None:
        await self._open_chat_page(chat_page, force_goto=False)
        items = await self._conversation_locator(chat_page)
        count = await items.count()
        for index in range(count):
            item = items.nth(index)
            with contextlib.suppress(Exception):
                if not await item.is_visible():
                    continue
                state, row_text = await self._read_conversation_state(item, conversation_states)
                if row_text:
                    state.last_row_text = row_text
                preview_text = await self._extract_conversation_preview_text(item)
                if preview_text and not state.last_friend_message:
                    state.last_friend_message = preview_text
                    state.replied_messages.add(preview_text)

    async def run_chat_flow(
        self,
        profile_dir: str,
        *,
        jobs_entry_url: str | None,
        chat_message_template: str,
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None = None,
        event_callback: Callable[[dict], None] | None = None,
        task_run_id: int | None = None,
        headless: bool = False,
        live_preview_enabled: bool = False,
        live_preview_interval_ms: int = 1000,
    ) -> None:
        await self.run_hybrid_flow(
            profile_dir,
            jobs_entry_url=jobs_entry_url,
            search_terms=[],
            keywords=[],
            cities=[],
            auto_send_chat=True,
            use_chat_template=True,
            chat_message_template=chat_message_template,
            enable_common_phrases=True,
            common_phrase_indexes=[1],
            enable_image_resume=False,
            image_resume_path="",
            auto_agree_wechat_card=False,
            boss_first_greeting_only=False,
            enable_follow_up_reply=True,
            follow_up_reply_rules=[],
            delivery_interval_seconds=3,
            max_deliveries_per_run=1,
            should_stop=should_stop,
            status_callback=status_callback,
            job_callback=None,
            job_precheck_callback=None,
            chat_event_callback=event_callback,
            confirm_login_callback=None,
            task_run_id=task_run_id,
            headless=headless,
            live_preview_enabled=live_preview_enabled,
            live_preview_interval_ms=live_preview_interval_ms,
        )

    async def _run_chat_page_test(
        self,
        chat_page,
        greeted_hrs: set[str],
        replied_messages: set[tuple[str, str]],
        sent_image_resume_hrs: set[str],
        use_chat_template: bool,
        chat_message_template: str,
        enable_common_phrases: bool,
        common_phrase_indexes: list[int],
        enable_image_resume: bool,
        image_resume_path: str,
        auto_agree_wechat_card: bool,
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None,
        chat_event_callback: Callable[[dict], None] | None,
    ) -> None:
        await self._open_chat_page(chat_page, status_callback)
        first_chat = await self._find_first_visible_conversation(chat_page, should_stop)
        if first_chat is None:
            if status_callback:
                status_callback("Boss 聊天测试模式：未找到任何可见会话")
            return

        hr_name = await self._safe_inner_text(first_chat.locator(self.selectors.conversation_name).first)
        company_name = await self._safe_inner_text(first_chat.locator(self.selectors.conversation_company).first)
        hr_key = self._build_hr_key(hr_name, company_name)

        if status_callback:
            status_callback(f"Boss 聊天测试模式：准备打开会话 {hr_name or '未知HR'} / {company_name or '未知公司'}")

        await self._open_conversation(first_chat)
        await chat_page.wait_for_timeout(2500)

        input_ready = await self._wait_for_chat_input(chat_page)
        if not input_ready:
            if status_callback:
                status_callback("Boss 聊天测试模式：会话已尝试打开，但输入框不可用")
            return

        current_job_name = await self._get_current_job_name(chat_page)
        greeting = ""
        if hr_key not in greeted_hrs:
            greeting = await self._send_greeting(
                chat_page,
                use_chat_template,
                chat_message_template,
                current_job_name,
                company_name,
                enable_common_phrases,
                common_phrase_indexes,
            )
            if greeting:
                greeted_hrs.add(hr_key)
                if status_callback:
                    status_callback(f"Boss 聊天测试模式：已发送消息 {greeting}")
                if chat_event_callback:
                    chat_event_callback(
                        {
                            "type": "greeting_sent",
                            "hr_key": hr_key,
                            "hr_name": hr_name,
                            "company_name": company_name,
                            "job_title": current_job_name,
                            "message": greeting,
                        }
                    )
        if enable_image_resume and hr_key not in sent_image_resume_hrs:
            image_sent = await self._send_image_resume(chat_page, image_resume_path)
            if image_sent:
                sent_image_resume_hrs.add(hr_key)
                if status_callback:
                    status_callback(f"Boss 聊天测试模式：已发送图片 {image_resume_path}")
        latest_friend_message = await self._get_last_friend_message(chat_page)
        normalized_message = self._normalize_text(latest_friend_message)
        if normalized_message:
            reply_key = (hr_key, normalized_message)
            if reply_key not in replied_messages:
                replied_messages.add(reply_key)
                reply_message = self._build_follow_up_reply(
                    latest_friend_message,
                    current_job_name,
                    company_name,
                    [],
                )
                if reply_message:
                    await self._send_text(chat_page, reply_message)
                    if status_callback:
                        status_callback(f"Boss 聊天测试模式：已发送跟进消息 {reply_message}")
        if auto_agree_wechat_card:
            await self._click_agree_button_if_needed(chat_page, hr_key, chat_event_callback, hr_name, company_name)

    async def _monitor_chat_messages(
        self,
        chat_page,
        conversation_states: dict[str, BossConversationState],
        use_chat_template: bool,
        chat_message_template: str,
        enable_common_phrases: bool,
        common_phrase_indexes: list[int],
        enable_image_resume: bool,
        image_resume_path: str,
        auto_agree_wechat_card: bool,
        boss_first_greeting_only: bool,
        enable_follow_up_reply: bool,
        follow_up_reply_rules: list[dict],
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None,
        chat_event_callback: Callable[[dict], None] | None,
        pending_job_queue: asyncio.Queue[dict] | None = None,
        delivery_finished: asyncio.Event | None = None,
    ) -> None:
        idle_timeout_seconds = 5 * 60
        last_activity_at = time.monotonic()
        pending_apply_tasks: list[BossChatTask] = []
        pending_inbound_tasks: list[BossChatTask] = []
        pending_task_keys: set[str] = set()

        while not should_stop():
            queued_activity = await self._drain_pending_apply_tasks(
                pending_job_queue,
                pending_apply_tasks,
                pending_task_keys,
                status_callback,
            )
            chat_task = self._pop_next_chat_task(pending_inbound_tasks, pending_apply_tasks, pending_task_keys)
            discovered_activity = False
            if chat_task is None:
                if not boss_first_greeting_only:
                    discovered_activity = await self._discover_inbound_chat_tasks(
                        chat_page,
                        conversation_states,
                        pending_inbound_tasks,
                        pending_task_keys,
                        should_stop,
                    )
                chat_task = self._pop_next_chat_task(pending_inbound_tasks, pending_apply_tasks, pending_task_keys)
            handled_activity = False
            if chat_task is not None:
                handled_activity = await self._process_chat_task(
                    chat_page,
                    chat_task,
                    conversation_states,
                    use_chat_template,
                    chat_message_template,
                    enable_common_phrases,
                    common_phrase_indexes,
                    enable_image_resume,
                    image_resume_path,
                    auto_agree_wechat_card,
                    boss_first_greeting_only,
                    enable_follow_up_reply,
                    follow_up_reply_rules,
                    should_stop,
                    status_callback,
                    chat_event_callback,
                )
            if queued_activity or discovered_activity or handled_activity:
                last_activity_at = time.monotonic()
            if delivery_finished is not None and not delivery_finished.is_set():
                await chat_page.wait_for_timeout(1500)
                continue
            remaining_seconds = idle_timeout_seconds - (time.monotonic() - last_activity_at)
            if delivery_finished is not None and remaining_seconds <= 0:
                if status_callback:
                    status_callback("Boss 聊天监听 5 分钟内无新消息，任务自动完成")
                return
            await chat_page.wait_for_timeout(1500)

    async def _drain_pending_apply_tasks(
        self,
        pending_job_queue: asyncio.Queue[dict] | None,
        pending_apply_tasks: list[BossChatTask],
        pending_task_keys: set[str],
        status_callback: Callable[[str], None] | None,
    ) -> bool:
        queued_activity = False
        while pending_job_queue is not None and not pending_job_queue.empty():
            job_payload = await pending_job_queue.get()
            company_name = str(job_payload.get("company_name", "") or "")
            job_title = str(job_payload.get("title", "") or "")
            dedupe_key = self._build_apply_task_key(job_payload)
            chat_task = BossChatTask(
                kind="apply_greeting",
                dedupe_key=dedupe_key,
                company_name=company_name,
                job_title=job_title,
                platform_job_id=str(job_payload.get("platform_job_id", "") or ""),
                detail_url=str(job_payload.get("detail_url", "") or ""),
                job_payload=dict(job_payload),
            )
            if self._enqueue_chat_task(pending_apply_tasks, pending_task_keys, chat_task):
                queued_activity = True
                logger.info(
                    "Boss queued apply chat task: company=%s job=%s platform_job_id=%s",
                    company_name,
                    job_title,
                    chat_task.platform_job_id,
                )
                if status_callback:
                    status_callback(f"Boss 聊天队列已加入投递会话: {company_name or '未知公司'} / {job_title or '未知岗位'}")
        return queued_activity

    @staticmethod
    def _enqueue_chat_task(
        pending_tasks: list[BossChatTask],
        pending_task_keys: set[str],
        chat_task: BossChatTask,
    ) -> bool:
        if chat_task.dedupe_key in pending_task_keys:
            return False
        pending_task_keys.add(chat_task.dedupe_key)
        pending_tasks.append(chat_task)
        return True

    @staticmethod
    def _pop_next_chat_task(
        pending_inbound_tasks: list[BossChatTask],
        pending_apply_tasks: list[BossChatTask],
        pending_task_keys: set[str],
    ) -> BossChatTask | None:
        next_task = pending_inbound_tasks.pop(0) if pending_inbound_tasks else None
        if next_task is None and pending_apply_tasks:
            next_task = pending_apply_tasks.pop(0)
        if next_task is not None:
            pending_task_keys.discard(next_task.dedupe_key)
        return next_task

    async def _discover_inbound_chat_tasks(
        self,
        chat_page,
        conversation_states: dict[str, BossConversationState],
        pending_inbound_tasks: list[BossChatTask],
        pending_task_keys: set[str],
        should_stop: Callable[[], bool],
        *,
        scan_limit: int = 12,
    ) -> bool:
        await self._open_chat_page(chat_page, force_goto=False)
        items = await self._conversation_locator(chat_page)
        count = await items.count()
        discovered = False
        logger.info("Boss inbound discovery scan: count=%s url=%s", count, chat_page.url)

        for index in range(min(count, scan_limit)):
            if should_stop():
                return discovered
            item = items.nth(index)
            with contextlib.suppress(Exception):
                if not await item.is_visible():
                    continue
                state, row_text = await self._read_conversation_state(item, conversation_states)
                previous_row_text = state.last_row_text
                logger.info(
                    "Boss inbound candidate [%s]: hr=%s company=%s changed=%s processing=%s suppressed=%s row_text=%s previous=%s",
                    index + 1,
                    state.hr_name,
                    state.company_name,
                    bool(row_text and row_text != previous_row_text),
                    state.processing,
                    time.monotonic() < state.suppress_until,
                    row_text,
                    previous_row_text,
                )
                if not row_text:
                    continue
                if row_text == previous_row_text:
                    state.last_row_text = row_text
                    continue
                if state.processing or time.monotonic() < state.suppress_until:
                    continue
                state.last_row_text = row_text
                chat_task = BossChatTask(
                    kind="inbound_reply",
                    dedupe_key=f"inbound:{state.hr_key}",
                    conversation_key=state.hr_key,
                    hr_name=state.hr_name,
                    company_name=state.company_name,
                )
                if self._enqueue_chat_task(pending_inbound_tasks, pending_task_keys, chat_task):
                    discovered = True
                    logger.info(
                        "Boss queued inbound chat task: hr=%s company=%s dedupe_key=%s",
                        state.hr_name,
                        state.company_name,
                        chat_task.dedupe_key,
                    )
        return discovered

    async def _process_chat_task(
        self,
        chat_page,
        chat_task: BossChatTask,
        conversation_states: dict[str, BossConversationState],
        use_chat_template: bool,
        chat_message_template: str,
        enable_common_phrases: bool,
        common_phrase_indexes: list[int],
        enable_image_resume: bool,
        image_resume_path: str,
        auto_agree_wechat_card: bool,
        boss_first_greeting_only: bool,
        enable_follow_up_reply: bool,
        follow_up_reply_rules: list[dict],
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None,
        chat_event_callback: Callable[[dict], None] | None,
    ) -> bool:
        if chat_task.kind == "apply_greeting":
            return await self._handle_apply_chat_task(
                chat_page,
                chat_task,
                conversation_states,
                use_chat_template,
                chat_message_template,
                enable_common_phrases,
                common_phrase_indexes,
                enable_image_resume,
                image_resume_path,
                auto_agree_wechat_card,
                boss_first_greeting_only,
                enable_follow_up_reply,
                follow_up_reply_rules,
                should_stop,
                status_callback,
                chat_event_callback,
            )
        if chat_task.kind == "inbound_reply":
            if boss_first_greeting_only:
                return False
            return await self._handle_inbound_chat_task(
                chat_page,
                chat_task,
                conversation_states,
                chat_message_template,
                auto_agree_wechat_card,
                enable_follow_up_reply,
                follow_up_reply_rules,
                should_stop,
                status_callback,
                chat_event_callback,
            )
        return False

    async def _handle_apply_chat_task(
        self,
        chat_page,
        chat_task: BossChatTask,
        conversation_states: dict[str, BossConversationState],
        use_chat_template: bool,
        chat_message_template: str,
        enable_common_phrases: bool,
        common_phrase_indexes: list[int],
        enable_image_resume: bool,
        image_resume_path: str,
        auto_agree_wechat_card: bool,
        boss_first_greeting_only: bool,
        enable_follow_up_reply: bool,
        follow_up_reply_rules: list[dict],
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None,
        chat_event_callback: Callable[[dict], None] | None,
    ) -> bool:
        job_payload = dict(chat_task.job_payload)
        conversation_item = await self._find_target_conversation(
            chat_page,
            company_name=chat_task.company_name,
            job_title=chat_task.job_title,
            should_stop=should_stop,
        )
        if conversation_item is None:
            self._emit_chat_delivery_update(
                chat_event_callback,
                job_payload,
                hr_key="",
                hr_name="",
                company_name=chat_task.company_name,
                job_title=chat_task.job_title,
                chat_sent=False,
                message="",
                send_status="failed",
            )
            if status_callback:
                status_callback(f"Boss 未在聊天列表定位到会话: {chat_task.company_name or '未知公司'} / {chat_task.job_title or '未知岗位'}")
            return False

        state, _ = await self._read_conversation_state(conversation_item, conversation_states)
        state.processing = True
        try:
            if state.greeting_sent:
                state.greeted_job_ids.add(chat_task.platform_job_id)
                self._emit_chat_delivery_update(
                    chat_event_callback,
                    job_payload,
                    hr_key=state.hr_key,
                    hr_name=state.hr_name,
                    company_name=state.company_name,
                    job_title=chat_task.job_title,
                    chat_sent=False,
                    message="",
                    send_status="skipped",
                )
                if status_callback:
                    status_callback(f"Boss 已跳过重复首聊: {state.hr_name or '未知HR'} / {state.company_name or '未知公司'}")
                return False

            await self._open_conversation(conversation_item)
            await chat_page.wait_for_timeout(1200)
            input_ready = await self._wait_for_chat_input(chat_page)
            current_job_name = await self._get_current_job_name(chat_page) or chat_task.job_title
            latest_friend_message = await self._refresh_state_after_open(chat_page, state)

            if not input_ready:
                self._emit_chat_delivery_update(
                    chat_event_callback,
                    job_payload,
                    hr_key=state.hr_key,
                    hr_name=state.hr_name,
                    company_name=state.company_name,
                    job_title=current_job_name,
                    chat_sent=False,
                    message="",
                    send_status="failed",
                )
                if status_callback:
                    status_callback(f"Boss 当前会话不可输入，跳过发送: {state.hr_name or '未知HR'}")
                return False

            greeting = await self._send_greeting(
                chat_page,
                use_chat_template,
                chat_message_template,
                current_job_name,
                state.company_name or chat_task.company_name,
                enable_common_phrases,
                common_phrase_indexes,
            )
            if greeting:
                state.greeting_sent = True
                state.greeted_job_ids.add(chat_task.platform_job_id)
                self._mark_outbound_suppression(state)
                if status_callback:
                    status_callback(f"Boss 已发送首聊: {state.hr_name or '未知HR'} / {greeting}")
                if chat_event_callback:
                    chat_event_callback(
                        {
                            "type": "greeting_sent",
                            "hr_key": state.hr_key,
                            "hr_name": state.hr_name,
                            "company_name": state.company_name,
                            "job_title": current_job_name,
                            "platform_job_id": chat_task.platform_job_id,
                            "detail_url": chat_task.detail_url,
                            "message": greeting,
                        }
                    )
            if enable_image_resume and not state.image_sent:
                image_sent = await self._send_image_resume(chat_page, image_resume_path)
                if image_sent:
                    state.image_sent = True
                    self._mark_outbound_suppression(state)
                    if status_callback:
                        status_callback(f"Boss 已发送图片: {image_resume_path}")

            normalized_message = self._normalize_text(latest_friend_message)
            if (
                not boss_first_greeting_only
                and enable_follow_up_reply
                and normalized_message
                and normalized_message not in state.replied_messages
            ):
                reply_message = self._build_follow_up_reply(
                    latest_friend_message,
                    current_job_name,
                    state.company_name or chat_task.company_name,
                    follow_up_reply_rules,
                )
                if reply_message:
                    state.replied_messages.add(normalized_message)
                    await self._send_text(chat_page, reply_message)
                    self._mark_outbound_suppression(state)
                    if chat_event_callback:
                        chat_event_callback(
                            {
                                "type": "follow_up_sent",
                                "hr_key": state.hr_key,
                                "hr_name": state.hr_name,
                                "company_name": state.company_name,
                                "job_title": current_job_name,
                                "platform_job_id": chat_task.platform_job_id,
                                "detail_url": chat_task.detail_url,
                                "message": reply_message,
                                "source_message": normalized_message,
                            }
                        )

            if auto_agree_wechat_card:
                await self._click_agree_button_if_needed(
                    chat_page,
                    state.hr_key,
                    chat_event_callback,
                    state.hr_name,
                    state.company_name,
                )

            self._emit_chat_delivery_update(
                chat_event_callback,
                job_payload,
                hr_key=state.hr_key,
                hr_name=state.hr_name,
                company_name=state.company_name,
                job_title=current_job_name,
                chat_sent=bool(greeting),
                message=greeting,
                send_status="sent" if greeting else "skipped",
            )
            return bool(greeting)
        finally:
            state.processing = False
            state.last_row_text = self._normalize_text(await self._safe_inner_text(conversation_item))

    async def _handle_inbound_chat_task(
        self,
        chat_page,
        chat_task: BossChatTask,
        conversation_states: dict[str, BossConversationState],
        chat_message_template: str,
        auto_agree_wechat_card: bool,
        enable_follow_up_reply: bool,
        follow_up_reply_rules: list[dict],
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None,
        chat_event_callback: Callable[[dict], None] | None,
    ) -> bool:
        state = conversation_states.get(chat_task.conversation_key)
        if state is None:
            return False

        conversation_item = await self._find_conversation_by_state(chat_page, state, should_stop)
        if conversation_item is None:
            return False

        state.processing = True
        try:
            await self._open_conversation(conversation_item)
            await chat_page.wait_for_timeout(1200)
            if status_callback:
                status_callback(f"Boss 正在检查会话: {state.hr_name or '未知HR'} / {state.company_name or '未知公司'}")
            current_job_name = await self._get_current_job_name(chat_page)
            latest_friend_message = await self._refresh_state_after_open(chat_page, state)
            normalized_message = self._normalize_text(latest_friend_message)
            if not normalized_message or normalized_message in state.replied_messages:
                if status_callback:
                    status_callback(f"Boss 检查会话后无需处理: {state.hr_name or '未知HR'} / 无新消息")
                if auto_agree_wechat_card:
                    await self._click_agree_button_if_needed(
                        chat_page,
                        state.hr_key,
                        chat_event_callback,
                        state.hr_name,
                        state.company_name,
                    )
                return False

            if not enable_follow_up_reply:
                state.replied_messages.add(normalized_message)
                if status_callback:
                    status_callback(f"Boss 检查会话后跳过回复: {state.hr_name or '未知HR'} / 已关闭自动跟进")
                return False

            reply_message = self._build_follow_up_reply(
                latest_friend_message,
                current_job_name,
                state.company_name,
                follow_up_reply_rules,
            )
            if not reply_message:
                state.replied_messages.add(normalized_message)
                if status_callback:
                    status_callback(f"Boss 检查会话后未命中回复规则: {state.hr_name or '未知HR'}")
                logger.info(
                    "Boss inbound chat skipped because no reply template matched: hr=%s company=%s message=%s",
                    state.hr_name,
                    state.company_name,
                    normalized_message,
                )
                return False

            input_ready = await self._wait_for_chat_input(chat_page)
            if not input_ready:
                return False

            state.replied_messages.add(normalized_message)
            await self._send_text(chat_page, reply_message)
            self._mark_outbound_suppression(state)
            if status_callback:
                status_callback(f"Boss 已响应新消息: {state.hr_name or '未知HR'} / {reply_message}")
            if chat_event_callback:
                chat_event_callback(
                    {
                        "type": "follow_up_sent",
                        "hr_key": state.hr_key,
                        "hr_name": state.hr_name,
                        "company_name": state.company_name,
                        "job_title": current_job_name,
                        "message": reply_message,
                        "source_message": normalized_message,
                    }
                )
            if auto_agree_wechat_card:
                await self._click_agree_button_if_needed(
                    chat_page,
                    state.hr_key,
                    chat_event_callback,
                    state.hr_name,
                    state.company_name,
                )
            return True
        finally:
            state.processing = False
            state.last_row_text = self._normalize_text(await self._safe_inner_text(conversation_item))

    @staticmethod
    def _build_apply_task_key(job_payload: dict) -> str:
        platform_job_id = str(job_payload.get("platform_job_id", "") or "").strip().lower()
        if platform_job_id:
            return f"apply:{platform_job_id}"
        company_name = BossAdapter._normalize_text(str(job_payload.get("company_name", "") or "")).lower()
        job_title = BossAdapter._normalize_text(str(job_payload.get("title", "") or "")).lower()
        return f"apply:{company_name}|{job_title}"

    async def _read_conversation_state(
        self,
        conversation_item,
        conversation_states: dict[str, BossConversationState],
    ) -> tuple[BossConversationState, str]:
        hr_name = await self._safe_inner_text(conversation_item.locator(self.selectors.conversation_name).first)
        company_name = await self._safe_inner_text(conversation_item.locator(self.selectors.conversation_company).first)
        row_text = await self._build_conversation_row_signature(conversation_item, hr_name, company_name)
        hr_key = self._build_hr_key(hr_name, company_name)
        if not hr_key:
            hr_key = self._normalize_text(f"{hr_name} {company_name} {row_text}").lower()
        state = conversation_states.get(hr_key)
        if state is None:
            state = BossConversationState(hr_key=hr_key)
            conversation_states[hr_key] = state
        state.hr_name = hr_name or state.hr_name
        state.company_name = company_name or state.company_name
        return state, row_text

    async def _build_conversation_row_signature(self, conversation_item, hr_name: str, company_name: str) -> str:
        raw_text = self._normalize_text(await self._safe_inner_text(conversation_item))
        if not raw_text:
            return ""

        signature = raw_text
        for token in (hr_name, company_name):
            normalized_token = self._normalize_text(token)
            if normalized_token:
                signature = signature.replace(normalized_token, " ")

        # Remove unstable row decorations that change when the item is selected
        # or when the site refreshes timestamps, but do not represent a new message.
        signature = re.sub(r"\b\d{1,2}:\d{2}\b", " ", signature)
        signature = re.sub(r"\d+\s*条?新消息", " ", signature)
        signature = re.sub(
            r"(在线|刚刚活跃|今日活跃|3日内活跃|本周活跃|本月活跃|半年前活跃|刚刚|昨天|前天)",
            " ",
            signature,
        )
        signature = self._normalize_text(signature)
        return signature or raw_text

    async def _find_conversation_by_state(self, chat_page, state: BossConversationState, should_stop: Callable[[], bool]):
        await self._open_chat_page(chat_page, force_goto=False)
        company_text = self._normalize_text(state.company_name).lower()
        hr_text = self._normalize_text(state.hr_name).lower()
        for _ in range(4):
            if should_stop():
                return None
            items = await self._conversation_locator(chat_page)
            count = await items.count()
            fallback = None
            for index in range(min(count, 15)):
                item = items.nth(index)
                with contextlib.suppress(Exception):
                    if not await item.is_visible():
                        continue
                    hr_name = await self._safe_inner_text(item.locator(self.selectors.conversation_name).first)
                    company_name = await self._safe_inner_text(item.locator(self.selectors.conversation_company).first)
                    hr_key = self._build_hr_key(hr_name, company_name)
                    row_text = self._normalize_text(await self._safe_inner_text(item)).lower()
                    if hr_key and hr_key == state.hr_key:
                        return item
                    if hr_text and hr_text in row_text and company_text and company_text in row_text:
                        return item
                    if hr_text and hr_text in row_text:
                        fallback = fallback or item
                    elif company_text and company_text in row_text:
                        fallback = fallback or item
            if fallback is not None:
                return fallback
            await chat_page.wait_for_timeout(800)
        return None

    async def _refresh_state_after_open(self, chat_page, state: BossConversationState) -> str:
        latest_friend_message = await self._get_last_friend_message(chat_page)
        normalized_message = self._normalize_text(latest_friend_message)
        if normalized_message:
            if not state.last_friend_message:
                state.replied_messages.add(normalized_message)
            state.last_friend_message = normalized_message
        return latest_friend_message

    @staticmethod
    def _mark_outbound_suppression(state: BossConversationState, seconds: float = 8.0) -> None:
        state.suppress_until = max(state.suppress_until, time.monotonic() + seconds)

    def _emit_chat_delivery_update(
        self,
        chat_event_callback: Callable[[dict], None] | None,
        job_payload: dict,
        *,
        hr_key: str,
        hr_name: str,
        company_name: str,
        job_title: str,
        chat_sent: bool,
        message: str,
        send_status: str,
    ) -> None:
        if chat_event_callback is None:
            return
        chat_event_callback(
            {
                "type": "chat_delivery_updated",
                "hr_key": hr_key,
                "hr_name": hr_name,
                "company_name": company_name,
                "job_title": job_title,
                "platform_job_id": job_payload.get("platform_job_id", ""),
                "detail_url": job_payload.get("detail_url", ""),
                "chat_sent": chat_sent,
                "message": message,
                "send_status": send_status,
            }
        )

    async def _find_first_visible_conversation(self, chat_page, should_stop: Callable[[], bool]):
        for _ in range(6):
            if should_stop():
                return None
            items = await self._conversation_locator(chat_page)
            count = await items.count()
            logger.info("Boss chat first conversation scan: count=%s url=%s", count, chat_page.url)
            for index in range(count):
                item = items.nth(index)
                with contextlib.suppress(Exception):
                    if await item.is_visible():
                        return item
            await chat_page.wait_for_timeout(1200)
        return None

    async def _find_next_changed_conversation(
        self,
        chat_page,
        conversation_snapshots: dict[str, str],
        should_stop: Callable[[], bool],
        *,
        scan_limit: int = 5,
    ):
        items = await self._conversation_locator(chat_page)
        count = await items.count()
        logger.info("Boss chat change scan: count=%s url=%s", count, chat_page.url)
        for index in range(min(count, scan_limit)):
            if should_stop():
                return None
            item = items.nth(index)
            with contextlib.suppress(Exception):
                if not await item.is_visible():
                    continue
                hr_name = await self._safe_inner_text(item.locator(self.selectors.conversation_name).first)
                company_name = await self._safe_inner_text(item.locator(self.selectors.conversation_company).first)
                hr_key = self._build_hr_key(hr_name, company_name)
                row_text = self._normalize_text(await self._safe_inner_text(item))
                previous_row_text = conversation_snapshots.get(hr_key, "")
                logger.info(
                    "Boss chat change candidate [%s]: hr=%s company=%s changed=%s row_text=%s previous=%s",
                    index + 1,
                    hr_name,
                    company_name,
                    row_text != previous_row_text,
                    row_text,
                    previous_row_text,
                )
                if row_text and row_text != previous_row_text:
                    return item
        return None

    async def _ensure_pages(self, context, jobs_url: str, chat_url: str):
        pages = [page for page in context.pages if not (page.url or "").startswith("chrome://")]
        jobs_page = pages[0] if pages else await context.new_page()
        chat_page = await context.new_page()

        await jobs_page.bring_to_front()
        await jobs_page.goto(jobs_url, wait_until="domcontentloaded")
        await jobs_page.wait_for_timeout(1500)

        await chat_page.bring_to_front()
        await chat_page.goto(chat_url, wait_until="domcontentloaded")
        await chat_page.wait_for_timeout(2500)

        logger.info(
            "Boss pages prepared: total=%s jobs_url=%s chat_url=%s context_pages=%s",
            len(context.pages),
            jobs_page.url,
            chat_page.url,
            [page.url for page in context.pages],
        )
        return jobs_page, chat_page

    def _launch_external_chrome(
        self,
        chrome_path: str,
        profile_path: Path,
        debug_port: int,
        entry_url: str,
        *,
        launch_options: dict | None = None,
    ) -> int:
        launch_options = launch_options or build_browser_launch_options(False)
        args = [
            f"--remote-debugging-port={debug_port}",
            f"--user-data-dir={profile_path}",
            "--new-window",
            "--no-first-run",
            "--no-default-browser-check",
            *launch_options["args"],
            entry_url or "about:blank",
        ]
        quoted_file = chrome_path.replace("'", "''")
        quoted_args = ", ".join("'" + str(arg).replace("'", "''") + "'" for arg in args)
        command = (
            f"$p = Start-Process -FilePath '{quoted_file}' "
            f"-ArgumentList @({quoted_args}) -PassThru; "
            "Write-Output $p.Id"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=True,
        )
        pid_text = (result.stdout or "").strip().splitlines()[-1]
        logger.info("Started external Chrome for Boss task: pid=%s port=%s", pid_text, debug_port)
        return int(pid_text)

    @staticmethod
    def _wait_for_cdp_endpoint(
        debug_port: int,
        timeout: int = 20,
        should_stop: Callable[[], bool] | None = None,
    ) -> str | None:
        deadline = time.time() + timeout
        url = f"http://127.0.0.1:{debug_port}/json/version"
        while time.time() < deadline:
            if should_stop and should_stop():
                return None
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    endpoint = payload.get("webSocketDebuggerUrl")
                    if endpoint:
                        return endpoint
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        return None

    @staticmethod
    def _terminate_chrome_process(chrome_pid: int | None) -> None:
        if not chrome_pid:
            return
        with contextlib.suppress(Exception):
            subprocess.run(
                ["taskkill", "/PID", str(chrome_pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )

    @staticmethod
    def _pick_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    @staticmethod
    def _resolve_chrome_executable() -> str:
        candidates = [
            shutil.which("chrome"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Google\Chrome Beta\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome Beta\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return str(candidate)
        raise RuntimeError("未找到可用的 Chrome/Edge 浏览器，请先安装 Chrome，或告诉我浏览器路径。")

    async def _preview_loop(
        self,
        context,
        preview_ref: dict[str, object],
        should_stop: Callable[[], bool],
        task_run_id: int,
        live_preview_interval_ms: int,
    ) -> None:
        interval = max(500, live_preview_interval_ms) / 1000
        while not should_stop():
            page = preview_ref.get("page")
            if (page is None or page.is_closed()) and context.pages:
                page = context.pages[-1]
                preview_ref["page"] = page
            if page is not None and not page.is_closed():
                with contextlib.suppress(Exception):
                    await live_preview_service.capture_page("boss", task_run_id, page)
            await asyncio.sleep(interval)

    async def is_login_required(self, jobs_page, chat_page) -> bool:
        for page in (jobs_page, chat_page):
            current_url = page.url.lower()
            if any(token in current_url for token in ("login", "passport", "signin")):
                return True

            user_name = page.locator(self.selectors.header_user_name)
            with contextlib.suppress(Exception):
                if await user_name.count():
                    user_text = self._normalize_text(await self._safe_inner_text(user_name.first))
                    if user_text:
                        return False

            user_link = page.locator(self.selectors.header_user_link)
            with contextlib.suppress(Exception):
                if await user_link.count():
                    return False

            logout_link = page.locator(self.selectors.header_logout_link)
            with contextlib.suppress(Exception):
                if await logout_link.count():
                    return False

            login_button = page.locator(self.selectors.header_login_button)
            with contextlib.suppress(Exception):
                if await login_button.count():
                    button_text = self._normalize_text(await self._safe_inner_text(login_button.first))
                    if "登录/注册" in button_text:
                        return True
        return False

    async def wait_for_login(
        self,
        jobs_page,
        chat_page,
        should_stop: Callable[[], bool],
        *,
        status_callback: Callable[[str], None] | None = None,
        confirm_login_callback: Callable[[], bool] | None = None,
    ) -> None:
        while not should_stop():
            if confirm_login_callback and confirm_login_callback():
                if await self.is_login_required(jobs_page, chat_page):
                    if status_callback:
                        status_callback("仍检测到 Boss 未登录，请完成登录后再次确认。")
                else:
                    if status_callback:
                        status_callback("已确认 Boss 登录完成，开始执行海投与聊天。")
                    return
            await jobs_page.wait_for_timeout(2000)

    async def _auto_scroll_job_list(self, jobs_page, should_stop: Callable[[], bool]) -> None:
        history: list[int] = []
        while not should_stop():
            await jobs_page.evaluate("window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' })")
            await jobs_page.wait_for_timeout(1200)
            count = await jobs_page.locator(self.selectors.job_card).count()
            history.append(count)
            history = history[-3:]
            if len(history) == 3 and len(set(history)) == 1:
                break
        await jobs_page.evaluate("window.scrollTo({ top: 0, behavior: 'smooth' })")
        await jobs_page.wait_for_timeout(800)

    async def _collect_job_cards(self, jobs_page, include_terms: list[str], city_terms: list[str]) -> list[dict]:
        return await jobs_page.evaluate(
            """
            ({ includeTerms, cityTerms }) => {
              const rawText = (value) => (value || "").replace(/\\s+/g, " ").trim();
              const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim().toLowerCase();
              const cards = Array.from(document.querySelectorAll("li.job-card-box"));
              return cards.map((card, index) => {
                const rawTitle = rawText(card.querySelector(".job-name")?.textContent);
                const title = normalize(rawTitle);
                const city = normalize(
                  card.querySelector(".job-address-desc")?.textContent ||
                  card.querySelector(".company-location")?.textContent ||
                  card.querySelector(".job-area")?.textContent
                );
                const headhunterAlt = normalize(card.querySelector(".job-tag-icon")?.getAttribute("alt"));
                const company = normalize(
                  card.querySelector(".company-name")?.textContent ||
                  card.querySelector(".boss-info-attr")?.textContent ||
                  card.querySelector("[class*=company]")?.textContent
                );
                const href = card.querySelector("a[href]")?.href || "";
                const salaryText = rawText(
                  card.querySelector(".job-salary")?.textContent ||
                  card.querySelector("[class*=salary]")?.textContent
                );
                const tagItems = Array.from(card.querySelectorAll(".tag-list li")).map((item) => rawText(item.textContent)).filter(Boolean);
                const platformJobId =
                  card.getAttribute("data-jobid") ||
                  card.dataset.jobid ||
                  (href.match(/job[_-]detail\\/(\\w+)/i) || href.match(/job[_-]card\\/(\\w+)/i) || [])[1] ||
                  "";
                const includeMatched = includeTerms.length === 0 || includeTerms.some((term) => title.includes(term));
                const cityMatched = cityTerms.length === 0 || cityTerms.some((term) => city.includes(term));
                return {
                  index,
                  title,
                  raw_title: rawTitle,
                  city,
                  company,
                  href,
                  salary_text: salaryText,
                  job_tags: tagItems,
                  platform_job_id: platformJobId,
                  is_headhunter: headhunterAlt.includes("猎头"),
                  include_matched: includeMatched,
                  city_matched: cityMatched,
                };
              }).filter((item) => item.include_matched && item.city_matched);
            }
            """,
            {"includeTerms": include_terms, "cityTerms": city_terms},
        )

    async def _open_job_and_extract(self, jobs_page, card: dict) -> dict:
        cards = jobs_page.locator(self.selectors.job_card)
        target_card = cards.nth(card["index"])
        await self._dismiss_greet_boss_dialog(jobs_page, max_rounds=4)
        click_error: Exception | None = None
        for attempt in range(2):
            try:
                await target_card.scroll_into_view_if_needed()
                await target_card.click(timeout=5000)
                click_error = None
                break
            except Exception as exc:  # noqa: BLE001
                click_error = exc
                logger.warning(
                    "Boss open job card click failed: title=%s index=%s attempt=%s",
                    card.get("title", ""),
                    card.get("index"),
                    attempt + 1,
                    exc_info=True,
                )
                await self._dismiss_greet_boss_dialog(jobs_page, max_rounds=6)
                await jobs_page.wait_for_timeout(600)
        if click_error is not None:
            raise RuntimeError(f"Boss 岗位卡片点击失败，可能被页面弹层遮挡: {card.get('title') or '未知岗位'}") from click_error
        await jobs_page.wait_for_timeout(2400)

        detail_title = await self._first_available_text(
            jobs_page,
            (
                self.selectors.detail_job_name_primary,
                self.selectors.detail_job_name_secondary,
                self.selectors.detail_job_name_tertiary,
            ),
        )
        card_title = str(card.get("raw_title") or card.get("title") or "").strip()
        title = self._resolve_job_title(detail_title, card_title)
        company_name = await self._extract_company_name(jobs_page) or (card.get("company") or "")
        salary_text = self._decode_boss_salary_text(
            await self._first_available_text(jobs_page, self.selectors.detail_salary_candidates)
            or str(card.get("salary_text", "") or "")
        )
        card_tags = card.get("job_tags") if isinstance(card.get("job_tags"), list) else []
        detail_limit_text = await self._first_available_text(jobs_page, self.selectors.detail_experience_candidates)
        experience_text, education_text = self._resolve_job_limits(detail_limit_text, card_tags)
        description = await self._extract_detail_description(jobs_page)
        tags = await self._extract_tags(jobs_page)
        recruiter_status = await self._extract_recruiter_status(jobs_page)

        detail_url = card.get("href") or jobs_page.url
        platform_job_id = card.get("platform_job_id") or self.extract_job_code(detail_url, title, company_name)
        salary_min, salary_max = self.normalize_salary(salary_text)

        return {
            "platform_job_id": platform_job_id,
            "title": title or card.get("title") or "未知岗位",
            "company_name": company_name or "未知公司",
            "salary_text": salary_text or "未标注",
            "salary_min": salary_min,
            "salary_max": salary_max,
            "description": description,
            "detail_url": detail_url,
            "tags": tags,
            "city": (card.get("city") or "").strip() or "未标注",
            "experience_text": experience_text or "不限",
            "education_text": education_text or "未标注",
            "recruiter_status": recruiter_status,
        }

    @staticmethod
    def _resolve_job_limits(detail_limit_text: str, card_tags: list[str]) -> tuple[str, str]:
        normalized_tags = [BossAdapter._normalize_text(str(item)) for item in card_tags if BossAdapter._normalize_text(str(item))]
        experience_text = normalized_tags[0] if normalized_tags else ""
        education_text = normalized_tags[1] if len(normalized_tags) > 1 else ""

        normalized_detail = BossAdapter._normalize_text(detail_limit_text)
        if normalized_detail:
            detail_parts = [
                part
                for part in re.split(r"[ /|｜·]+", normalized_detail)
                if BossAdapter._normalize_text(part)
            ]
            for part in detail_parts:
                normalized_part = BossAdapter._normalize_text(part)
                if not experience_text and re.search(r"经验|应届|年", normalized_part):
                    experience_text = normalized_part
                if (
                    not education_text
                    and re.search(r"学历|大专|专科|本科|硕士|博士|中专|高中|初中|不限", normalized_part)
                ):
                    education_text = normalized_part

        return experience_text or "不限", education_text or "未标注"

    async def _start_chat_from_job(self, jobs_page) -> dict[str, str | bool]:
        await self._dismiss_greet_boss_dialog(jobs_page, max_rounds=4)
        chat_btn = jobs_page.locator(self.selectors.chat_button).first
        if await chat_btn.count() == 0:
            return {"success": False, "reason": "未找到立即沟通按钮"}

        before_text = self._normalize_text(await self._safe_inner_text(chat_btn))
        if before_text != "立即沟通":
            return {"success": False, "reason": f"当前按钮状态为“{before_text or '未知'}”，未执行沟通"}

        await chat_btn.scroll_into_view_if_needed()
        await jobs_page.wait_for_timeout(1000)
        await chat_btn.click()
        await jobs_page.wait_for_timeout(1200)
        await self._dismiss_greet_boss_dialog(jobs_page, max_rounds=8)
        await jobs_page.wait_for_timeout(1200)

        after_text = self._normalize_text(await self._safe_inner_text(chat_btn))
        if after_text and after_text != before_text:
            return {
                "success": True,
                "reason": f"已点击立即沟通，按钮状态由“{before_text}”变为“{after_text}”",
            }
        return {
            "success": True,
            "reason": f"已点击立即沟通，当前按钮状态“{after_text or before_text}”",
        }

    async def _dismiss_greet_boss_dialog(self, jobs_page, *, max_rounds: int = 6) -> bool:
        dismissed = False
        for attempt in range(max_rounds):
            dialog_visible = False
            layer_visible = False
            stay_button = jobs_page.locator(self.selectors.stay_on_page_button).filter(has_text="留在此页").first
            dialog = jobs_page.locator(self.selectors.greet_boss_dialog).first
            layer = jobs_page.locator(self.selectors.greet_boss_layer).first

            with contextlib.suppress(Exception):
                dialog_visible = await dialog.count() > 0 and await dialog.is_visible()
            with contextlib.suppress(Exception):
                layer_visible = await layer.count() > 0 and await layer.is_visible()

            stay_clicked = False
            with contextlib.suppress(Exception):
                if await stay_button.count() > 0 and await stay_button.is_visible():
                    await self._log_click_target(stay_button, f"greet_boss_stay_button:{attempt + 1}")
                    await stay_button.click(timeout=2500)
                    stay_clicked = True
            if not stay_clicked and (dialog_visible or layer_visible):
                alt_button = jobs_page.get_by_text("留在此页").first
                with contextlib.suppress(Exception):
                    if await alt_button.count() > 0 and await alt_button.is_visible():
                        await self._log_click_target(alt_button, f"greet_boss_alt_button:{attempt + 1}")
                        await alt_button.click(timeout=2500)
                        stay_clicked = True

            if stay_clicked:
                dismissed = True
                logger.info("Boss dismissed greet-boss dialog via stay button: attempt=%s", attempt + 1)
                await jobs_page.wait_for_timeout(900)
                continue

            if not dialog_visible and not layer_visible:
                return dismissed

            with contextlib.suppress(Exception):
                await jobs_page.keyboard.press("Escape")
                await jobs_page.wait_for_timeout(500)
            with contextlib.suppress(Exception):
                if await layer.count() > 0 and await layer.is_visible():
                    await layer.click(position={"x": 8, "y": 8}, timeout=1500)
                    await jobs_page.wait_for_timeout(500)
            logger.info(
                "Boss greet-boss dialog still visible after dismissal attempt: attempt=%s dialog=%s layer=%s",
                attempt + 1,
                dialog_visible,
                layer_visible,
            )
            await jobs_page.wait_for_timeout(400)
        return dismissed

    async def _handle_chat_after_apply(
        self,
        chat_page,
        job_payload: dict,
        greeted_hrs: set[str],
        replied_messages: set[tuple[str, str]],
        sent_image_resume_hrs: set[str],
        use_chat_template: bool,
        chat_message_template: str,
        enable_common_phrases: bool,
        common_phrase_indexes: list[int],
        enable_image_resume: bool,
        image_resume_path: str,
        auto_agree_wechat_card: bool,
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None,
        event_callback: Callable[[dict], None] | None,
    ) -> dict[str, str | bool]:
        logger.info(
            "Boss immediate chat after apply: company=%s job=%s page_url=%s",
            job_payload.get("company_name", ""),
            job_payload.get("title", ""),
            chat_page.url,
        )
        await chat_page.bring_to_front()
        if status_callback:
            status_callback(
                f"Boss 投递成功，准备切换聊天页: {job_payload.get('company_name', '')} / {job_payload.get('title', '')}"
            )
        await self._open_chat_page(chat_page, status_callback, force_goto=False)

        latest_chat = await self._find_target_conversation(
            chat_page,
            company_name=job_payload.get("company_name", ""),
            job_title=job_payload.get("title", ""),
            should_stop=should_stop,
        )
        if latest_chat is None:
            if event_callback:
                event_callback(
                    {
                        "type": "chat_delivery_updated",
                        "company_name": job_payload.get("company_name", ""),
                        "job_title": job_payload.get("title", ""),
                        "platform_job_id": job_payload.get("platform_job_id", ""),
                        "detail_url": job_payload.get("detail_url", ""),
                        "chat_sent": False,
                        "message": "",
                    }
                )
            if status_callback:
                status_callback(f"Boss 已发起沟通，但未在聊天页定位到会话: {job_payload.get('title', '')}")
            return {"chat_sent": False, "chat_message": ""}

        await self._log_click_target(latest_chat, "target_conversation")
        hr_name = await self._safe_inner_text(latest_chat.locator(self.selectors.conversation_name).first)
        company_name = await self._safe_inner_text(latest_chat.locator(self.selectors.conversation_company).first)
        hr_key = self._build_hr_key(hr_name, company_name)

        await self._open_conversation(latest_chat)
        await chat_page.wait_for_timeout(2500)
        if status_callback:
            status_callback(f"Boss 已定位聊天会话: {hr_name or '未知HR'} / {company_name or '未知公司'}")

        input_ready = await self._wait_for_chat_input(chat_page)
        if not input_ready:
            if event_callback:
                event_callback(
                    {
                        "type": "chat_delivery_updated",
                        "hr_key": hr_key,
                        "hr_name": hr_name,
                        "company_name": company_name,
                        "job_title": job_payload.get("title", ""),
                        "platform_job_id": job_payload.get("platform_job_id", ""),
                        "detail_url": job_payload.get("detail_url", ""),
                        "chat_sent": False,
                        "message": "",
                    }
                )
            if status_callback:
                status_callback("Boss 聊天页未找到可用输入框，未执行发送动作")
            return {"chat_sent": False, "chat_message": ""}

        current_job_name = await self._get_current_job_name(chat_page) or job_payload.get("title", "")
        if status_callback:
            status_callback(f"Boss 正在聊天: {hr_name} / {company_name}")

        greeting = ""
        if hr_key not in greeted_hrs:
            greeting = await self._send_greeting(
                chat_page,
                use_chat_template,
                chat_message_template,
                current_job_name,
                company_name,
                enable_common_phrases,
                common_phrase_indexes,
            )
            if greeting:
                greeted_hrs.add(hr_key)
                if status_callback:
                    status_callback(f"Boss 已发送首条消息: {greeting}")
            if event_callback:
                event_callback(
                    {
                        "type": "greeting_sent",
                        "hr_key": hr_key,
                        "hr_name": hr_name,
                        "company_name": company_name,
                        "job_title": current_job_name,
                        "message": greeting,
                    }
                )
            await chat_page.wait_for_timeout(1800)
        if enable_image_resume and hr_key not in sent_image_resume_hrs:
            image_sent = await self._send_image_resume(chat_page, image_resume_path)
            if image_sent:
                sent_image_resume_hrs.add(hr_key)
                if status_callback:
                    status_callback(f"Boss 已发送图片: {image_resume_path}")

        latest_friend_message = await self._get_last_friend_message(chat_page)
        normalized_message = self._normalize_text(latest_friend_message)
        if normalized_message:
            reply_key = (hr_key, normalized_message)
            if reply_key not in replied_messages:
                replied_messages.add(reply_key)
                reply_message = self._build_follow_up_reply(
                    latest_friend_message,
                    current_job_name,
                    company_name,
                    [],
                )
                if reply_message:
                    await self._send_text(chat_page, reply_message)
                    if event_callback:
                        event_callback(
                            {
                                "type": "follow_up_sent",
                                "hr_key": hr_key,
                                "hr_name": hr_name,
                                "company_name": company_name,
                                "job_title": current_job_name,
                                "message": reply_message,
                                "source_message": normalized_message,
                            }
                        )

        if auto_agree_wechat_card:
            await self._click_agree_button_if_needed(chat_page, hr_key, event_callback, hr_name, company_name)
        if event_callback:
            event_callback(
                {
                    "type": "chat_delivery_updated",
                    "hr_key": hr_key,
                    "hr_name": hr_name,
                    "company_name": company_name,
                    "job_title": current_job_name,
                    "platform_job_id": job_payload.get("platform_job_id", ""),
                    "detail_url": job_payload.get("detail_url", ""),
                    "chat_sent": bool(greeting),
                    "message": greeting,
                }
            )
        return {"chat_sent": bool(greeting), "chat_message": greeting}

    async def _find_target_conversation(self, chat_page, *, company_name: str, job_title: str, should_stop: Callable[[], bool]):
        company_text = self._normalize_text(company_name).lower()
        job_text = self._normalize_text(job_title).lower()

        for attempt in range(8):
            if should_stop():
                return None
            items = await self._conversation_locator(chat_page)
            count = await items.count()
            logger.info(
                "Boss chat conversation scan: attempt=%s company=%s job=%s count=%s url=%s",
                attempt + 1,
                company_text,
                job_text,
                count,
                chat_page.url,
            )
            await self._log_conversation_candidates(items, count, limit=8)
            best_item = None
            for index in range(count):
                item = items.nth(index)
                if not await item.is_visible():
                    continue
                hr_company = self._normalize_text(
                    await self._safe_inner_text(item.locator(self.selectors.conversation_company).first)
                ).lower()
                row_text = self._normalize_text(await self._safe_inner_text(item)).lower()
                if company_text and company_text in hr_company:
                    logger.info(
                        "Boss target conversation matched by company: index=%s target_company=%s row_company=%s row_text=%s",
                        index,
                        company_text,
                        hr_company,
                        row_text,
                    )
                    return item
                if company_text and company_text in row_text:
                    best_item = best_item or item
                if job_text and job_text in row_text:
                    logger.info(
                        "Boss target conversation matched by job: index=%s target_job=%s row_text=%s",
                        index,
                        job_text,
                        row_text,
                    )
                    return item
            if best_item is not None:
                logger.info("Boss target conversation fallback matched partial company row")
                return best_item
            await chat_page.wait_for_timeout(1500)

        return None

    async def _open_conversation(self, conversation_item) -> None:
        for selector in self.selectors.conversation_figure:
            figure = conversation_item.locator(selector)
            if await figure.count():
                await self._log_click_target(figure.first, f"open_conversation:{selector}")
                await figure.first.click()
                return
        await self._log_click_target(conversation_item, "open_conversation:item")
        await conversation_item.click()

    async def _get_current_job_name(self, chat_page) -> str:
        for selector in (
            self.selectors.detail_job_name_primary,
            self.selectors.detail_job_name_secondary,
            self.selectors.detail_job_name_tertiary,
        ):
            locator = chat_page.locator(selector)
            with contextlib.suppress(Exception):
                count = await locator.count()
                for index in range(count):
                    item = locator.nth(index)
                    if not await item.is_visible():
                        continue
                    text = self._normalize_text(await self._safe_inner_text(item))
                    if text:
                        await self._log_click_target(item, f"current_job_name:{selector}:{index}")
                        return text
        return ""

    async def _send_text(self, chat_page, message: str) -> None:
        input_box = chat_page.locator(self.selectors.input_box).first
        await input_box.wait_for(state="visible", timeout=8000)
        await input_box.click()
        await input_box.fill("")
        await input_box.fill(message)
        send_button = chat_page.locator(self.selectors.send_button)
        if await send_button.count():
            await send_button.first.click()
        else:
            await input_box.press("Enter")
        await chat_page.wait_for_timeout(800)

    async def _send_greeting(
        self,
        chat_page,
        use_chat_template: bool,
        template: str,
        job_title: str,
        company_name: str,
        enable_common_phrases: bool,
        common_phrase_indexes: list[int],
    ) -> str:
        if enable_common_phrases:
            phrase = await self._send_common_phrase(chat_page, common_phrase_indexes)
            if phrase:
                return phrase
        if not use_chat_template:
            return ""
        message = self._format_chat_message(template, job_title, company_name)
        await self._send_text(chat_page, message)
        return message

    async def _send_common_phrase(self, chat_page, common_phrase_indexes: list[int]) -> str:
        selected_indexes = sorted({index for index in common_phrase_indexes if index > 0}) or [1]
        sent_texts: list[str] = []
        for selected_index in selected_indexes:
            last_friend_message_before = self._normalize_text(await self._get_last_friend_message(chat_page))
            phrase_item, text = await self._pick_common_phrase_item(chat_page, selected_index)
            if phrase_item is None:
                fallback_text = await self._click_common_phrase_via_dom(chat_page, selected_index)
                if not fallback_text:
                    logger.warning("Boss common phrase item not found: index=%s", selected_index)
                    continue
                await chat_page.wait_for_timeout(1200)
                await self._flush_phrase_if_needed(chat_page, fallback_text, last_friend_message_before)
                sent_texts.append(fallback_text)
                continue
            try:
                await self._log_click_target(phrase_item, f"common_phrase_item:{selected_index}")
                await phrase_item.click()
            except Exception:  # noqa: BLE001
                logger.warning("Boss common phrase locator click failed, fallback to DOM click: index=%s", selected_index, exc_info=True)
                fallback_text = await self._click_common_phrase_via_dom(chat_page, selected_index)
                if not fallback_text:
                    continue
                text = fallback_text
            if not text:
                continue
            await chat_page.wait_for_timeout(1200)
            if text:
                await self._flush_phrase_if_needed(chat_page, text, last_friend_message_before)
                sent_texts.append(text)
        if sent_texts:
            return " | ".join(sent_texts)
        return ""

    async def _pick_common_phrase_item(self, chat_page, selected_index: int):
        for selector in self.selectors.common_phrase_button:
            button = chat_page.locator(selector).first
            with contextlib.suppress(Exception):
                if await button.count() and await button.is_visible():
                    await self._log_click_target(button, f"common_phrase_button:{selector}")
                    await button.click()
                    await chat_page.wait_for_timeout(900)
                    phrase_items = await self._find_visible_common_phrase_items(chat_page)
                    await self._log_common_phrase_candidates(phrase_items)
                    if selected_index > len(phrase_items):
                        logger.info(
                            "Boss common phrase index out of range: selected=%s available=%s",
                            selected_index,
                            len(phrase_items),
                        )
                        continue
                    phrase_item = phrase_items[selected_index - 1]
                    text = self._normalize_text(await self._safe_inner_text(phrase_item))
                    return phrase_item, text
        return None, ""

    async def _click_common_phrase_via_dom(self, chat_page, selected_index: int) -> str:
        result = await chat_page.evaluate(
            """
            ({ selectedIndex }) => {
              const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
              const hidden = (el) => {
                if (!el) return true;
                const style = window.getComputedStyle(el);
                return style.display === "none" || style.visibility === "hidden" || style.opacity === "0";
              };
              const ignored = ["附件", "举报", "拉黑", "屏蔽", "不感兴趣"];
              const roots = [
                'ul[data-v-f115c50c=""]',
                ".panel-resume.sentence-popover ul",
                ".sentence-popover ul",
                ".dict-popover ul",
                ".dict-list ul",
                ".boss-popup__wrapper ul",
                ".dict-panel ul",
                ".common-words-panel ul",
              ];
              const items = [];
              const seen = new Set();
              for (const rootSelector of roots) {
                const root = document.querySelector(rootSelector);
                if (!root || hidden(root)) continue;
                for (const item of root.querySelectorAll("li")) {
                  if (hidden(item)) continue;
                  const text = normalize(item.textContent);
                  if (!text) continue;
                  if (ignored.some((token) => text.includes(token))) continue;
                  if (seen.has(text)) continue;
                  seen.add(text);
                  items.push(item);
                }
              }
              const target = items[selectedIndex - 1];
              if (!target) {
                return {
                  clicked: false,
                  text: "",
                  candidates: items.map((item) => normalize(item.textContent)),
                };
              }
              target.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
              target.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true }));
              target.click();
              return {
                clicked: true,
                text: normalize(target.textContent),
                candidates: items.map((item) => normalize(item.textContent)),
                html: (target.outerHTML || "").slice(0, 500),
              };
            }
            """,
            {"selectedIndex": selected_index},
        )
        candidates = result.get("candidates") or []
        logger.info(
            "Boss common phrase DOM fallback: index=%s clicked=%s text=%s candidates=%s html=%s",
            selected_index,
            result.get("clicked"),
            result.get("text", ""),
            candidates,
            result.get("html", ""),
        )
        return self._normalize_text(str(result.get("text", "") or ""))

    async def _flush_phrase_if_needed(self, chat_page, expected_text: str, previous_friend_message: str) -> None:
        normalized_expected = self._normalize_text(expected_text)
        if not normalized_expected:
            return

        latest_friend_message = self._normalize_text(await self._get_last_friend_message(chat_page))
        if latest_friend_message and latest_friend_message != previous_friend_message and normalized_expected in latest_friend_message:
            return

        current_input_text = await self._get_chat_input_text(chat_page)
        if normalized_expected not in current_input_text:
            return

        send_button = chat_page.locator(self.selectors.send_button).first
        with contextlib.suppress(Exception):
            if await send_button.count() and await send_button.is_visible():
                await send_button.click()
                await chat_page.wait_for_timeout(900)
                return

        input_box = chat_page.locator(self.selectors.input_box).first
        with contextlib.suppress(Exception):
            await input_box.press("Enter")
            await chat_page.wait_for_timeout(900)

    async def _get_chat_input_text(self, chat_page) -> str:
        input_box = chat_page.locator(self.selectors.input_box).first
        with contextlib.suppress(Exception):
            if await input_box.count() == 0:
                return ""
            value = await input_box.input_value()
            normalized_value = self._normalize_text(value)
            if normalized_value:
                return normalized_value
        with contextlib.suppress(Exception):
            text = await input_box.inner_text()
            normalized_text = self._normalize_text(text)
            if normalized_text:
                return normalized_text
        with contextlib.suppress(Exception):
            text_content = await input_box.text_content()
            return self._normalize_text(text_content)
        return ""

    async def _send_image_resume(self, chat_page, image_resume_path: str) -> bool:
        if not image_resume_path.strip():
            return False
        if not os.path.exists(image_resume_path):
            logger.warning("Boss image resume path not found: %s", image_resume_path)
            return False
        for selector in (
            '.toolbar-btn-content.icon.btn-sendimg input[type="file"]',
            'input[type="file"]',
        ):
            file_input = chat_page.locator(selector).first
            with contextlib.suppress(Exception):
                if await file_input.count():
                    await file_input.set_input_files(image_resume_path)
                    await chat_page.wait_for_timeout(2000)
                    logger.info("Boss image resume uploaded via selector=%s path=%s", selector, image_resume_path)
                    return True
        return False

    async def _wait_for_chat_input(self, chat_page) -> bool:
        input_box = chat_page.locator(self.selectors.input_box).first
        for _ in range(6):
            with contextlib.suppress(Exception):
                if await input_box.count() and await input_box.is_visible():
                    return True
            await chat_page.wait_for_timeout(1000)
        return False

    async def _conversation_locator(self, chat_page):
        for selector in self.selectors.conversation_items:
            locator = chat_page.locator(selector)
            with contextlib.suppress(Exception):
                if await locator.count():
                    return locator
        return chat_page.locator("li")

    async def _find_visible_common_phrase_items(self, chat_page):
        for panel_selector in self.selectors.common_phrase_panels:
            panel = chat_page.locator(panel_selector)
            with contextlib.suppress(Exception):
                panel_count = await panel.count()
                for panel_index in range(panel_count):
                    current_panel = panel.nth(panel_index)
                    if not await current_panel.is_visible():
                        continue
                    matched_items = []
                    seen_keys: set[str] = set()
                    for item_selector in self.selectors.common_phrase_items:
                        items = current_panel.locator(item_selector)
                        count = await items.count()
                        for index in range(count):
                            item = items.nth(index)
                            if not await item.is_visible():
                                continue
                            text = self._normalize_text(await self._safe_inner_text(item))
                            if not text:
                                continue
                            if any(token in text for token in ("附件", "举报", "拉黑", "屏蔽", "不感兴趣")):
                                continue
                            key = text
                            if key in seen_keys:
                                continue
                            seen_keys.add(key)
                            matched_items.append(item)
                    if matched_items:
                        return matched_items
        return []

    async def _extract_conversation_preview_text(self, conversation_item) -> str:
        with contextlib.suppress(Exception):
            text = self._normalize_text(await self._safe_inner_text(conversation_item))
            if text:
                return text
        return ""

    async def _log_common_phrase_candidates(self, phrase_items) -> None:
        for index, item in enumerate(phrase_items, start=1):
            with contextlib.suppress(Exception):
                text = self._normalize_text(await self._safe_inner_text(item))
                html = await item.evaluate(
                    """(el) => {
                        const outer = el.outerHTML || '';
                        return outer.length > 500 ? outer.slice(0, 500) + '...' : outer;
                    }"""
                )
                logger.info("Boss common phrase candidate [%s]: text=%s html=%s", index, text, html)

    async def _log_click_target(self, locator, label: str) -> None:
        with contextlib.suppress(Exception):
            html = await locator.evaluate(
                """(el) => {
                    const outer = el.outerHTML || '';
                    return outer.length > 500 ? outer.slice(0, 500) + '...' : outer;
                }"""
            )
            text = self._normalize_text(await self._safe_inner_text(locator))
            logger.info("Boss click target [%s]: text=%s html=%s", label, text, html)

    async def _open_chat_page(
        self,
        chat_page,
        status_callback: Callable[[str], None] | None = None,
        *,
        force_goto: bool = True,
    ) -> None:
        current_url = (chat_page.url or "").lower()
        should_goto = force_goto or "/web/geek/chat" not in current_url
        if should_goto:
            try:
                logger.info("Boss open chat page: force_goto=%s current_url=%s", force_goto, chat_page.url)
                await chat_page.goto(settings.boss_chat_url, wait_until="domcontentloaded")
                await chat_page.wait_for_timeout(3200)
            except Exception:  # noqa: BLE001
                logger.warning("Boss chat page direct goto failed", exc_info=True)
        else:
            logger.info("Boss open chat page skipped direct goto because current page already chat: %s", chat_page.url)
            return

        for selector in self.selectors.message_nav_link:
            link = chat_page.locator(selector).first
            with contextlib.suppress(Exception):
                if await link.count() and await link.is_visible():
                    if status_callback:
                        status_callback("Boss 正在通过消息入口进入聊天页")
                    await link.click()
                    await chat_page.wait_for_load_state("domcontentloaded")
                    await chat_page.wait_for_timeout(2500)
                    return

    async def _log_conversation_candidates(self, items, count: int, limit: int = 8) -> None:
        upper = min(count, limit)
        for index in range(upper):
            item = items.nth(index)
            with contextlib.suppress(Exception):
                if not await item.is_visible():
                    continue
                name = self._normalize_text(await self._safe_inner_text(item.locator(self.selectors.conversation_name).first))
                company = self._normalize_text(
                    await self._safe_inner_text(item.locator(self.selectors.conversation_company).first)
                )
                row_text = self._normalize_text(await self._safe_inner_text(item))
                html = await item.evaluate(
                    """(el) => {
                        const outer = el.outerHTML || '';
                        return outer.length > 500 ? outer.slice(0, 500) + '...' : outer;
                    }"""
                )
                logger.info(
                    "Boss conversation candidate [%s]: name=%s company=%s row_text=%s html=%s",
                    index + 1,
                    name,
                    company,
                    row_text,
                    html,
                )

    async def _click_agree_button_if_needed(
        self,
        chat_page,
        hr_key: str,
        event_callback: Callable[[dict], None] | None,
        hr_name: str,
        company_name: str,
    ) -> bool:
        card_wrap = chat_page.locator(self.selectors.card_wrap)
        if await card_wrap.count() == 0:
            return False
        buttons = chat_page.locator(self.selectors.card_button)
        count = await buttons.count()
        for index in range(count):
            button = buttons.nth(index)
            text = self._normalize_text(await self._safe_inner_text(button))
            if "同意" not in text:
                continue
            class_name = self._normalize_text(await button.get_attribute("class"))
            aria_disabled = self._normalize_text(await button.get_attribute("aria-disabled")).lower()
            if "disabled" in class_name.lower() or aria_disabled == "true":
                continue
            clicked = False
            with contextlib.suppress(Exception):
                await button.scroll_into_view_if_needed()
                await button.click(timeout=4000)
                clicked = True
            if not clicked:
                with contextlib.suppress(Exception):
                    await button.evaluate("(el) => el.click()")
                    clicked = True
            if not clicked:
                logger.warning("Boss agree button click skipped because element remained blocked")
                continue
            if event_callback:
                event_callback(
                    {
                        "type": "card_agree_clicked",
                        "hr_key": hr_key,
                        "hr_name": hr_name,
                        "company_name": company_name,
                        "message": "检测到卡片消息并已点击同意",
                    }
                )
            await chat_page.wait_for_timeout(800)
            return True
        return False

    async def _get_last_friend_message(self, chat_page) -> str:
        message_items = chat_page.locator(self.selectors.friend_message_items)
        count = await message_items.count()
        if count == 0:
            return ""
        latest_item = message_items.nth(count - 1)
        text_locator = latest_item.locator(self.selectors.friend_message_text)
        if await text_locator.count():
            texts = await text_locator.all_inner_texts()
            return " ".join(self._normalize_text(text) for text in texts if self._normalize_text(text))
        return self._normalize_text(await self._safe_inner_text(latest_item))

    async def _extract_company_name(self, page) -> str:
        locator = page.locator(self.selectors.detail_company_attr).first
        if await locator.count() == 0:
            return ""
        text = self._normalize_text(await self._safe_inner_text(locator))
        if not text:
            return ""
        return text.split("·", maxsplit=1)[0].strip()

    async def _extract_tags(self, page) -> list[str]:
        seen: set[str] = set()
        for selector in self.selectors.detail_tags_candidates:
            locator = page.locator(selector)
            if await locator.count():
                texts = await locator.all_inner_texts()
                tags = []
                for text in texts:
                    normalized = self._normalize_text(text)
                    if not normalized or normalized in seen:
                        continue
                    seen.add(normalized)
                    tags.append(normalized)
                if tags:
                    return tags
        return []

    async def _extract_detail_description(self, page) -> str:
        for selector in self.selectors.detail_description_candidates:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                continue

            text = ""
            with contextlib.suppress(Exception):
                text = await locator.evaluate(
                    """(el) => {
                        if (!(el instanceof HTMLElement)) {
                            return el?.textContent || '';
                        }
                        return el.innerText || el.textContent || '';
                    }"""
                )
            normalized = self._normalize_multiline_text(text)
            if normalized:
                return normalized
        return ""

    async def _extract_recruiter_status(self, page) -> str:
        online_tag = page.locator(self.selectors.recruiter_online_tag)
        if await online_tag.count():
            online_text = self._normalize_text(await self._safe_inner_text(online_tag.first))
            if online_text == "在线":
                return "在线"
        active = page.locator(self.selectors.recruiter_active_time)
        if await active.count():
            active_text = self._normalize_text(await self._safe_inner_text(active.first))
            if active_text:
                return active_text
        return "未知"

    async def _first_available_text(self, page, selectors: tuple[str, ...]) -> str:
        for selector in selectors:
            locator = page.locator(selector)
            if await locator.count():
                text = self._normalize_text(await self._safe_inner_text(locator.first))
                if text:
                    return text
        return ""

    @staticmethod
    def _normalize_terms(values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = BossAdapter._normalize_text(value).lower()
            if item and item not in seen:
                seen.add(item)
                normalized.append(item)
        return normalized

    @staticmethod
    def extract_job_code(detail_url: str, title: str = "", company_name: str = "") -> str:
        if detail_url:
            parsed = urlparse(detail_url)
            candidate = parsed.path.rstrip("/").split("/")[-1]
            if candidate:
                return candidate
        fallback = f"{title}-{company_name}".strip("-")
        return fallback or "boss-job"

    @staticmethod
    def normalize_salary(raw_salary: str) -> tuple[float | None, float | None]:
        salary = BossAdapter._normalize_salary_text(raw_salary)
        if "-" not in salary:
            value = BossAdapter._parse_salary_part(salary)
            return value, value
        left, right = salary.split("-", maxsplit=1)
        left, right = BossAdapter._inherit_range_units(left, right)
        return BossAdapter._parse_salary_part(left), BossAdapter._parse_salary_part(right)

    @staticmethod
    def _normalize_salary_text(raw_salary: str) -> str:
        salary = BossAdapter._decode_boss_salary_text(raw_salary)
        salary = salary.replace("－", "-").replace("—", "-").replace("–", "-").replace("~", "-")
        salary = salary.replace("至", "-").replace("到", "-")
        salary = salary.replace("元", "").replace("千", "k").replace("仟", "k").replace("万", "w")
        salary = salary.replace("K", "k").replace("W", "w")
        salary = re.sub(r"[·•xX×＊*]\s*\d+\s*薪", "", salary, flags=re.IGNORECASE)
        salary = re.sub(r"\d+\s*薪", "", salary, flags=re.IGNORECASE)
        salary = re.sub(r"(税前|税后|以上|以下|左右|面议|每月|每个月|每年|年薪|月薪|薪资|工资)", "", salary, flags=re.IGNORECASE)
        salary = re.sub(r"\s*/\s*(月|年)", "", salary, flags=re.IGNORECASE)
        salary = re.sub(r"\s+", "", salary)
        return salary.strip("- ")

    @staticmethod
    def _parse_salary_part(value: str) -> float | None:
        digits = re.findall(r"\d+(?:\.\d+)?", value)
        if not digits:
            return None
        number = float(digits[0])
        normalized = value.lower()
        if "w" in normalized:
            return number * 10
        if "k" in normalized:
            return number
        return number / 1000 if number > 1000 else number

    @staticmethod
    def _inherit_range_units(left: str, right: str) -> tuple[str, str]:
        unit_pattern = re.compile(r"[wk]", re.IGNORECASE)
        left_unit_match = unit_pattern.search(left)
        right_unit_match = unit_pattern.search(right)
        left_unit = left_unit_match.group(0).lower() if left_unit_match else ""
        right_unit = right_unit_match.group(0).lower() if right_unit_match else ""

        if not left_unit and right_unit:
            left = f"{left}{right_unit}"
        elif left_unit and not right_unit:
            right = f"{right}{left_unit}"
        return left, right

    @staticmethod
    def _build_hr_key(hr_name: str, company_name: str) -> str:
        return f"{hr_name.strip()}-{company_name.strip()}".strip("-").lower()

    @staticmethod
    def _resolve_job_title(detail_title: str, card_title: str) -> str:
        normalized_detail = BossAdapter._normalize_text(detail_title)
        normalized_card = BossAdapter._normalize_text(card_title)
        if not normalized_detail:
            return normalized_card
        if not normalized_card:
            return normalized_detail
        lowered_detail = normalized_detail.lower()
        lowered_card = normalized_card.lower()
        if lowered_detail == lowered_card or lowered_detail in lowered_card or lowered_card in lowered_detail:
            return normalized_detail
        logger.warning(
            "Boss detail title mismatched card title, fallback to card title: detail=%s card=%s",
            normalized_detail,
            normalized_card,
        )
        return normalized_card

    @staticmethod
    def _decode_boss_salary_text(value: str | None) -> str:
        normalized = BossAdapter._normalize_text(value)
        if not normalized:
            return ""
        return normalized.translate(BOSS_SALARY_FONT_MAP)

    @staticmethod
    def _normalize_text(value: str | None) -> str:
        if not value:
            return ""
        value = re.sub(r"\s+", " ", value)
        value = re.sub(r"[\u200b-\u200d\ufeff]", "", value)
        return value.strip()

    @staticmethod
    def _normalize_multiline_text(value: str | None) -> str:
        if not value:
            return ""
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        value = re.sub(r"[\u200b-\u200d\ufeff]", "", value)
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        value = re.sub(r"[ \t]{2,}", " ", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    @staticmethod
    def _safe_format_template(template: str, **values: str) -> str:
        class SafeDict(dict):
            def __missing__(self, key: str) -> str:
                return "{" + key + "}"

        return template.format_map(SafeDict(values))

    @staticmethod
    def _format_chat_message(template: str, job_title: str, company_name: str) -> str:
        if not template.strip():
            template = "您好，我正在关注贵司的 {job_title} 岗位，希望进一步沟通。"
        safe_job_title = job_title or "相关岗位"
        safe_company_name = company_name or "贵司"
        return BossAdapter._safe_format_template(
            template,
            job_title=safe_job_title,
            company_name=safe_company_name,
            latest_message="",
        )

    def _build_follow_up_reply(
        self,
        latest_message: str,
        job_title: str,
        company_name: str,
        follow_up_reply_rules: list[dict],
    ) -> str:
        normalized_message = self._normalize_text(latest_message)
        normalized = normalized_message.lower()
        if not normalized or not follow_up_reply_rules:
            return ""
        safe_job_title = job_title or "相关岗位"
        safe_company_name = company_name or "贵司"
        for raw_rule in follow_up_reply_rules:
            if not isinstance(raw_rule, dict):
                continue
            enabled = bool(raw_rule.get("enabled", True))
            template = self._normalize_text(str(raw_rule.get("reply_template", "") or ""))
            keywords = raw_rule.get("keywords", [])
            normalized_keywords = []
            if isinstance(keywords, list):
                normalized_keywords = [
                    self._normalize_text(str(keyword)).lower()
                    for keyword in keywords
                    if self._normalize_text(str(keyword))
                ]
            if not enabled or not template or not normalized_keywords:
                continue
            if any(keyword in normalized for keyword in normalized_keywords):
                return self._safe_format_template(
                    template,
                    job_title=safe_job_title,
                    company_name=safe_company_name,
                    latest_message=normalized_message,
                )
        return ""

    @staticmethod
    async def _safe_inner_text(locator) -> str:
        try:
            return (await locator.inner_text()).strip()
        except Exception:  # noqa: BLE001
            return ""

    def import_resume_snapshot(
        self,
        profile_dir: str,
        *,
        headless: bool | None = None,
        target_url: str = "https://www.zhipin.com/web/geek/resume",
    ) -> dict:
        return run_async_blocking(self._import_resume_snapshot(profile_dir, headless=headless, target_url=target_url))

    def sync_master_resume(
        self,
        profile_dir: str,
        master_resume: dict,
        *,
        headless: bool | None = None,
        target_url: str = "https://www.zhipin.com/web/geek/resume",
    ) -> dict:
        return run_async_blocking(
            self._sync_master_resume(
                profile_dir,
                master_resume,
                headless=headless,
                target_url=target_url,
            )
        )

    async def _sync_master_resume(
        self,
        profile_dir: str,
        master_resume: dict,
        *,
        headless: bool | None = None,
        target_url: str = "https://www.zhipin.com/web/geek/resume",
    ) -> dict:
        from playwright.async_api import async_playwright

        payload = self._build_resume_sync_payload(master_resume)
        launch_headless = settings.browser_headless if headless is None else headless
        use_persistent_context = bool(launch_headless and not settings.browser_fake_headless)

        async with async_playwright() as playwright:
            browser = None
            chrome_pid: int | None = None
            if use_persistent_context:
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    **build_browser_launch_options(launch_headless),
                )
            else:
                chrome_path = self._resolve_chrome_executable()
                profile_path = Path(profile_dir)
                if not profile_path.is_absolute():
                    profile_path = Path.cwd() / profile_path
                profile_path.mkdir(parents=True, exist_ok=True)

                debug_port = self._pick_free_port()
                chrome_pid = self._launch_external_chrome(
                    chrome_path,
                    profile_path,
                    debug_port,
                    target_url,
                    launch_options=build_browser_launch_options(launch_headless),
                )
                ws_endpoint = self._wait_for_cdp_endpoint(debug_port, timeout=20, should_stop=lambda: False)
                if not ws_endpoint:
                    raise RuntimeError("BOSS 简历同步浏览器已启动，但未能连接到调试端口。")
                browser = await playwright.chromium.connect_over_cdp(ws_endpoint)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()

            await context.add_init_script(STEALTH_INIT_SCRIPT)
            await context.add_init_script(LAODENG_INIT_SCRIPT)
            page = context.pages[0] if context.pages else await context.new_page()

            try:
                await page.goto(target_url, wait_until="domcontentloaded")
                with contextlib.suppress(Exception):
                    await page.wait_for_timeout(1500)
                if await self.is_login_required(page, page):
                    raise RuntimeError("BOSS 当前未登录，请先在账号配置浏览器中完成登录后再同步。")
                await self._wait_resume_ready(page)

                updated_sections: list[str] = []
                detail_messages: list[str] = []

                basic_result = await self._sync_resume_basic_info(page, payload)
                if basic_result["updated"]:
                    updated_sections.append("个人信息")
                detail_messages.extend(basic_result["messages"])

                summary_result = await self._sync_resume_summary(page, payload.get("summary", ""))
                if summary_result["updated"]:
                    updated_sections.append("个人优势")
                detail_messages.extend(summary_result["messages"])

                await self._reload_resume_page(page, target_url)
                preference_result = await self._sync_resume_expectation(page, payload)
                if preference_result["updated"]:
                    updated_sections.append("求职期望")
                detail_messages.extend(preference_result["messages"])

                work_result = await self._sync_resume_work_experiences(page, payload.get("work_experiences", []))
                if work_result["updated"]:
                    updated_sections.append("工作经历")
                detail_messages.extend(work_result["messages"])

                await self._reload_resume_page(page, target_url)
                project_result = await self._sync_resume_project_experiences(page, payload.get("project_experiences", []))
                if project_result["updated"]:
                    updated_sections.append("项目经历")
                detail_messages.extend(project_result["messages"])

                await self._reload_resume_page(page, target_url)
                education_result = await self._sync_resume_education_experiences(
                    page, payload.get("education_experiences", [])
                )
                if education_result["updated"]:
                    updated_sections.append("教育经历")
                detail_messages.extend(education_result["messages"])

                await self._reload_resume_page(page, target_url)
                certificate_result = await self._sync_resume_certificates(page, payload.get("certificates", []))
                if certificate_result["updated"]:
                    updated_sections.append("资格证书")
                detail_messages.extend(certificate_result["messages"])

                await self._reload_resume_page(page, target_url)
                skill_result = await self._sync_resume_skills(page, payload.get("skills", []))
                if skill_result["updated"]:
                    updated_sections.append("专业技能")
                detail_messages.extend(skill_result["messages"])

                if not updated_sections:
                    raise RuntimeError("BOSS 未找到可写回的简历字段，请确认简历页结构没有变化。")

                message = f"BOSS 同步完成：{'、'.join(updated_sections)}已更新"
                if detail_messages:
                    message = f"{message}；{'；'.join(dict.fromkeys(detail_messages))}"
                return {
                    "updated_sections": updated_sections,
                    "message": message,
                }
            finally:
                with contextlib.suppress(Exception):
                    await context.close()
                if browser is not None:
                    with contextlib.suppress(Exception):
                        await browser.close()
                self._terminate_chrome_process(chrome_pid)

    async def _sync_resume_basic_info(self, page, payload: dict) -> dict:
        basic_info = dict(payload.get("basic_info") or {})
        if not any(str(basic_info.get(key) or "").strip() for key in ("full_name", "wechat_id", "email")):
            return {"updated": False, "messages": []}

        opened = await self._open_resume_editor(page, "个人信息", edit_selector='a.link-edit[ka="user-resume-edit-userinfo"], .resume-baseInfo a.link-edit, .resume-userinfo a.link-edit')
        if not opened:
            return {"updated": False, "messages": ["BOSS 个人信息编辑入口未找到，已跳过"]}

        try:
            result = await page.evaluate(
                """({ payload }) => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    const canReplace = (el, nextValue) => {
                        const currentValue = normalize(el?.value || '');
                        const targetValue = normalize(nextValue);
                        if (!targetValue) return false;
                        if (!currentValue) return true;
                        return currentValue === targetValue;
                    };
                    const dispatchInput = (el, value) => {
                        if (!el || el.disabled) return false;
                        el.focus();
                        el.value = value;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    };
                    const root = Array.from(document.querySelectorAll('*')).find((el) => {
                        const title = el.querySelector('h3');
                        return title && normalize(title.textContent) === '编辑个人信息';
                    });
                    if (!root) return { ok: false };
                    const inputs = Array.from(root.querySelectorAll('input[type="text"], input:not([type]), textarea')).filter((el) => el.offsetParent !== null);
                    const fullNameInput = inputs.find((el) => normalize(el.placeholder).includes('姓名'));
                    const wechatInput = inputs.find((el) => normalize(el.placeholder).includes('微信号'));
                    const emailInput = inputs.find((el) => normalize(el.placeholder).includes('邮箱'));
                    let changed = false;
                    let skippedFullName = false;
                    if (payload.full_name && fullNameInput) {
                        if (canReplace(fullNameInput, payload.full_name)) {
                            changed = dispatchInput(fullNameInput, payload.full_name) || changed;
                        } else {
                            skippedFullName = true;
                        }
                    }
                    if (payload.wechat_id && wechatInput && canReplace(wechatInput, payload.wechat_id)) {
                        changed = dispatchInput(wechatInput, payload.wechat_id) || changed;
                    }
                    if (payload.email && emailInput && canReplace(emailInput, payload.email)) {
                        changed = dispatchInput(emailInput, payload.email) || changed;
                    }
                    return { ok: true, changed, skippedFullName };
                }""",
                {"payload": basic_info},
            )
        except Exception:
            await self._dismiss_unsaved_resume_editor(page)
            return {"updated": False, "messages": ["BOSS 个人信息表单写入失败，已跳过"]}

        if not result.get("ok"):
            await self._dismiss_unsaved_resume_editor(page)
            return {"updated": False, "messages": ["BOSS 个人信息表单未成功打开，已跳过"]}
        if not result.get("changed"):
            await self._dismiss_unsaved_resume_editor(page)
            messages = ["BOSS 姓名已存在且不可直接覆盖，已保留原值"] if result.get("skippedFullName") else []
            return {"updated": False, "messages": messages}

        saved = await self._finish_resume_editor(page, "编辑个人信息")
        if not saved:
            return {"updated": False, "messages": ["BOSS 个人信息保存失败，已保留原值"]}
        messages = ["BOSS 姓名已存在且不可直接覆盖，已保留原值"] if result.get("skippedFullName") else []
        return {"updated": True, "messages": messages}

    async def _sync_resume_summary(self, page, summary: str) -> dict:
        summary_text = self._normalize_multiline_text(summary)
        if not summary_text:
            return {"updated": False, "messages": []}

        opened = await self._open_resume_editor(page, "个人优势", item_text="擅长领域", edit_selector=".resume-advantage a.link-edit")
        if not opened:
            return {"updated": False, "messages": ["BOSS 个人优势编辑入口未找到，已跳过"]}

        try:
            result = await page.evaluate(
                """({ summary }) => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    const root = Array.from(document.querySelectorAll('*')).find((el) => {
                        const title = el.querySelector('h3');
                        return title && normalize(title.textContent) === '编辑个人优势';
                    });
                    const textarea = root?.querySelector('textarea');
                    if (!textarea) return { ok: false };
                    textarea.focus();
                    textarea.value = summary;
                    textarea.dispatchEvent(new Event('input', { bubbles: true }));
                    textarea.dispatchEvent(new Event('change', { bubbles: true }));
                    return { ok: true };
                }""",
                {"summary": summary_text},
            )
        except Exception:
            await self._dismiss_unsaved_resume_editor(page)
            return {"updated": False, "messages": ["BOSS 个人优势写入失败，已跳过"]}

        if not result.get("ok"):
            await self._dismiss_unsaved_resume_editor(page)
            return {"updated": False, "messages": ["BOSS 个人优势表单未成功打开，已跳过"]}
        saved = await self._finish_resume_editor(page, "编辑个人优势")
        if not saved:
            return {"updated": False, "messages": ["BOSS 个人优势保存失败，已保留原值"]}
        return {"updated": True, "messages": []}

    async def _sync_resume_expectation(self, page, payload: dict) -> dict:
        if not any(
            (
                payload.get("desired_role"),
                payload.get("locations"),
                payload.get("salary_min_k") is not None and payload.get("salary_max_k") is not None,
            )
        ):
            return {"updated": False, "messages": []}

        opened = await self._open_resume_editor(
            page,
            "期望职位",
            edit_selector=".resume-expectList a.link-edit, .resume-purpose a.link-edit, a.link-edit[ka^='user-resume-edit-expectation']",
            add_selector=".resume-expectList a.link-add, .resume-purpose a.link-add",
        )
        if not opened:
            return {"updated": False, "messages": ["BOSS 求职期望编辑入口未找到，已跳过"]}

        try:
            result = await page.evaluate(
                """async ({ payload }) => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    const lower = (value) => normalize(value).toLowerCase();
                    const toCode = (item) => {
                        const value = item?.settingValue ?? item?.value ?? item?.code ?? item?.id ?? null;
                        if (value == null || value === '') return null;
                        const numeric = Number(value);
                        return Number.isFinite(numeric) ? numeric : null;
                    };
                    const hasMeaningfulCode = (item) => {
                        const code = toCode(item);
                        return code != null && code > 0;
                    };
                    const resolvePositionToken = (value) => {
                        const text = lower(value);
                        if (!text) return '';
                        if (['java', '后端', '后台', '服务端', 'golang', 'python', 'php', '全栈', 'full stack', 'fullstack'].some((keyword) => text.includes(keyword))) {
                            return '后端开发';
                        }
                        if (['前端', 'vue', 'react', 'uniapp', '小程序', '移动端', 'android', 'ios'].some((keyword) => text.includes(keyword))) {
                            return '前端/移动开发';
                        }
                        if (['测试', 'qa'].some((keyword) => text.includes(keyword))) {
                            return '测试';
                        }
                        if (['运维', 'devops'].some((keyword) => text.includes(keyword))) {
                            return '运维/技术支持';
                        }
                        return normalize(value);
                    };
                    const unique = (items) => {
                        const result = [];
                        const seen = new Set();
                        for (const item of items || []) {
                            const value = normalize(item);
                            if (!value) continue;
                            const key = value.toLowerCase();
                            if (seen.has(key)) continue;
                            seen.add(key);
                            result.push(value);
                        }
                        return result;
                    };
                    const cleanCityToken = (value) =>
                        normalize(value).split(/[·\\-]/)[0].replace(/市辖区|地区|特别行政区/g, '').trim();
                    const getChildren = (item) => item?.subLevelModelList || item?.children || item?.items || [];
                    const flatten = (items, parents = []) => {
                        const result = [];
                        for (const item of items || []) {
                            result.push({ item, parents });
                            const children = getChildren(item);
                            if (children.length) result.push(...flatten(children, parents.concat(item)));
                        }
                        return result;
                    };
                    const matchNode = (items, token) => {
                        const normalized = lower(token);
                        if (!normalized) return null;
                        let exactFallback = null;
                        let fuzzy = null;
                        for (const entry of flatten(items)) {
                            const name = lower(entry.item?.name || entry.item?.label || entry.item?.text || '');
                            if (!name) continue;
                            if (name === normalized) {
                                if (hasMeaningfulCode(entry.item)) return entry;
                                exactFallback = exactFallback || entry;
                                continue;
                            }
                            if (!fuzzy && (name.includes(normalized) || normalized.includes(name))) fuzzy = entry;
                        }
                        return fuzzy || exactFallback;
                    };
                    const vm = document.querySelector('.expectation-form')?.__vue__;
                    const form = vm?._data?.formData || vm?.formData;
                    if (!vm || !form) return { ok: false };
                    const output = { ok: true, updated: false, roleUpdated: false, cityUpdated: false, salaryUpdated: false };
                    const roleToken = resolvePositionToken(payload?.roles?.[0] || payload?.desired_role || '');
                    const cityToken = unique((payload?.locations || []).map(cleanCityToken).filter(Boolean))[0] || '';
                    if (roleToken) {
                        const role = matchNode(vm._data?.positionList || [], roleToken);
                        if (role?.item) {
                            form.positionName = role.item.name || roleToken;
                            const roleCode = toCode(role.item);
                            if (roleCode != null) form.position = roleCode;
                            if (role.item.positionType != null) form.positionType = role.item.positionType;
                            output.updated = true;
                            output.roleUpdated = true;
                        }
                    }
                    if (cityToken) {
                        const city = matchNode(vm._data?.cityOptions || [], cityToken);
                        if (city?.item) {
                            const cityCode = toCode(city.item);
                            form.locationName = city.item.name || cityToken;
                            if (cityCode != null) form.location = cityCode;
                            const parentCodes = city.parents.map((item) => toCode(item)).filter((item) => item != null && item !== -1);
                            if (cityCode != null) form.cityValue = [...parentCodes, cityCode];
                            output.updated = true;
                            output.cityUpdated = true;
                        }
                    }
                    if (payload?.salary_min_k != null && payload?.salary_max_k != null && payload.salary_min_k > 0 && payload.salary_max_k >= payload.salary_min_k) {
                        form.lowSalary = payload.salary_min_k;
                        form.highSalary = payload.salary_max_k;
                        form.salaryScope = [payload.salary_min_k, payload.salary_max_k];
                        form.salaryDesc = `${payload.salary_min_k}k-${payload.salary_max_k}k`;
                        form.salaryDescNew = `${payload.salary_min_k}k-${payload.salary_max_k}k`;
                        output.updated = true;
                        output.salaryUpdated = true;
                    }
                    if (output.updated) {
                        await vm.handleSubmit();
                        await new Promise((resolve) => setTimeout(resolve, 800));
                    }
                    return output;
                }""",
                {"payload": payload},
            )
        except Exception:
            await self._dismiss_unsaved_resume_editor(page)
            return {"updated": False, "messages": ["BOSS 求职期望写入失败，已跳过"]}

        messages: list[str] = []
        if not result.get("ok"):
            await self._dismiss_unsaved_resume_editor(page)
            return {"updated": False, "messages": ["BOSS 求职期望表单未成功打开，已跳过"]}
        if not result.get("updated"):
            await self._dismiss_unsaved_resume_editor(page)
            return {"updated": False, "messages": ["求职期望未匹配到可更新字段，已保留原值"]}
        if result.get("roleUpdated") is False and payload.get("desired_role"):
            messages.append("BOSS 期望职位未精确匹配，已保留原值")
        if result.get("cityUpdated") is False and payload.get("locations"):
            messages.append("BOSS 工作城市未匹配成功，已保留原值")
        if result.get("salaryUpdated") is False and payload.get("salary_text"):
            messages.append("BOSS 薪资未匹配成功，已保留原值")
        if await self._has_resume_error_dialog(page):
            await self._dismiss_unsaved_resume_editor(page)
            messages.append("BOSS 求职期望保存失败，已保留原值")
            return {"updated": False, "messages": messages}
        saved = await self._wait_editor_closed(page, "编辑求职期望")
        if not saved:
            saved = await self._finish_resume_editor(page, "编辑求职期望")
        if not saved:
            await self._dismiss_unsaved_resume_editor(page)
            messages.append("BOSS 求职期望保存后未正常关闭，已保留原值")
            return {"updated": False, "messages": messages}
        return {"updated": True, "messages": messages}

    async def _sync_resume_work_experiences(self, page, items: list[dict]) -> dict:
        if not items:
            return {"updated": False, "messages": []}

        updated_count = 0
        messages: list[str] = []
        existing_count = await page.locator('a.link-edit[ka^="user-resume-edit-workexp"]').count()

        for index, item in enumerate(items):
            opened = await self._open_resume_editor(
                page,
                "工作/实习经历",
                item_text=self._normalize_text(item.get("company") or ""),
                edit_selector=(
                    f'a.link-edit[ka="user-resume-edit-workexp{index}"]'
                    if index < existing_count
                    else ".resume-workExpList a.link-edit"
                ),
                add_selector='[ka="user-resume-add-workexp"], .resume-workExpList a.link-add',
            )
            if not opened:
                messages.append(f"BOSS 工作经历第 {index + 1} 条未找到编辑入口，已跳过")
                continue
            await page.wait_for_timeout(600)
            try:
                result = await page.evaluate(
                    """async ({ payload }) => {
                    const container = document.querySelector('.work-experience-container');
                    const vm = container?.__vue__;
                    const form = vm?._data?.formData || vm?.formData;
                    if (!vm || !form) {
                        return { ok: false, reason: '未找到工作经历表单。' };
                    }
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    const lower = (value) => normalize(value).toLowerCase();
                    const includesAny = (value, keywords) => keywords.some((keyword) => lower(value).includes(keyword));
                    const findIndustry = (token) => {
                        const normalized = lower(token);
                        for (const group of vm._data?.industryList || []) {
                            for (const child of group.subLevelModelList || []) {
                                const name = lower(child?.name || '');
                                if (!name) continue;
                                if (name === normalized || name.includes(normalized) || normalized.includes(name)) {
                                    return child;
                                }
                            }
                        }
                        return null;
                    };
                    const resolvePositionToken = (value) => {
                        const text = lower(value);
                        if (!text) return '后端开发';
                        if (includesAny(text, ['java', '后端', '后台', '服务端', 'golang', 'python', 'php', '全栈', 'full stack', 'fullstack'])) {
                            return '后端开发';
                        }
                        if (includesAny(text, ['前端', 'vue', 'react', 'uniapp', '小程序', '移动端', 'android', 'ios'])) {
                            return '前端/移动开发';
                        }
                        if (includesAny(text, ['测试', 'qa'])) {
                            return '测试';
                        }
                        if (includesAny(text, ['运维', 'devops'])) {
                            return '运维/技术支持';
                        }
                        return normalize(value);
                    };
                    const findPosition = (token) => {
                        const normalized = lower(resolvePositionToken(token));
                        for (const group of vm._data?.positionList || []) {
                            for (const child of group.subLevelModelList || []) {
                                const name = lower(child?.name || '');
                                if (!name) continue;
                                if (name === normalized || name.includes(normalized) || normalized.includes(name)) {
                                    return { parent: group, child };
                                }
                            }
                        }
                        return null;
                    };

                    const position = findPosition(payload?.title || payload?.role || '');
                    const industry = findIndustry(payload?.industry || '计算机软件');
                    if (industry) {
                        form.industryCode = industry.code || industry.id || industry.value || '';
                        form.industryName = industry.name || payload?.industry || '计算机软件';
                    } else {
                        form.industryCode = '';
                        form.industryName = normalize(payload?.industry || '计算机软件');
                    }

                    form.companyName = normalize(payload?.company || '');
                    form.department = normalize(payload?.department || '');
                    form.startDate = normalize(payload?.start_date || '');
                    form.endDate = normalize(payload?.end_date || '');
                    form.workContent = normalize(payload?.work_content || payload?.description || '');
                    form.workPerformance = normalize(payload?.work_performance || '');
                    form.workType = !!payload?.is_intern;
                    form.isPublic = true;
                    form.emphasis = Array.isArray(payload?.tags) ? payload.tags.slice(0, 6).map((item) => normalize(item)).filter(Boolean) : [];

                    if (position?.child) {
                        vm._data.positionName = position.child.name || resolvePositionToken(payload?.title || '');
                        vm._data.positionCode = position.child.code || position.child.id || position.child.value || 0;
                        vm._data.positionLv2 = position.parent?.code || position.parent?.id || position.parent?.value || 0;
                        form.position = vm._data.positionCode || 0;
                        form.positionName = vm._data.positionName || '';
                        form.customPositionName = vm._data.positionName || '';
                    } else {
                        const fallbackName = resolvePositionToken(payload?.title || payload?.role || '');
                        vm._data.positionName = fallbackName;
                        form.position = 0;
                        form.positionName = fallbackName;
                        form.customPositionName = fallbackName;
                    }

                    await vm.handleSubmit();
                    await new Promise((resolve) => setTimeout(resolve, 700));
                    return {
                        ok: true,
                        positionMatched: !!position?.child,
                        industryMatched: !!industry,
                    };
                }""",
                    {"payload": item},
                )
            except Exception:
                messages.append(f"BOSS 工作经历第 {index + 1} 条未能打开编辑表单，已跳过")
                continue
            await self._confirm_resume_risk_dialog(page)
            await page.wait_for_timeout(1000)
            saved = await self._wait_editor_closed(page, "编辑工作/实习经历")
            if not saved:
                saved = await self._finish_resume_editor(page, "编辑工作/实习经历")
            if not saved:
                messages.append(f"BOSS 工作经历第 {index + 1} 条保存失败，已保留原值")
                await self._dismiss_unsaved_resume_editor(page)
                continue
            updated_count += 1
            if not result.get("positionMatched"):
                messages.append(f"BOSS 工作经历第 {index + 1} 条职位未精确匹配，已使用近似职位")
            if not result.get("industryMatched"):
                messages.append(f"BOSS 工作经历第 {index + 1} 条所属行业未精确匹配，已尝试默认行业")

        return {"updated": updated_count > 0, "messages": messages}

    async def _sync_resume_project_experiences(self, page, items: list[dict]) -> dict:
        if not items:
            return {"updated": False, "messages": []}

        updated_count = 0
        messages: list[str] = []
        existing_count = await page.locator('a.link-edit[ka^="user-resume-edit-project"]').count()

        for index, item in enumerate(items):
            opened = await self._open_resume_editor(
                page,
                "项目经历",
                item_text=self._normalize_text(item.get("name") or ""),
                edit_selector=(
                    f'a.link-edit[ka="user-resume-edit-project{index}"]'
                    if index < existing_count
                    else ".resume-project a.link-edit"
                ),
                add_selector='[ka="user-resume-add-project"], .resume-project a.link-add',
            )
            if not opened:
                messages.append(f"BOSS 项目经历第 {index + 1} 条未找到编辑入口，已跳过")
                continue
            await page.wait_for_timeout(600)
            try:
                await page.evaluate(
                    """async ({ payload }) => {
                    const container = document.querySelector('.resume-project .item-form');
                    const vm = container?.__vue__;
                    const form = vm?._data?.formData || vm?.formData;
                    if (!vm || !form) {
                        throw new Error('未找到项目经历表单。');
                    }
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    form.name = normalize(payload?.name || '');
                    form.roleName = normalize(payload?.role || payload?.title || '');
                    form.url = normalize(payload?.url || '');
                    form.startDate = normalize(payload?.start_date || '');
                    form.endDate = normalize(payload?.end_date || '');
                    form.description = normalize(payload?.description || '');
                    form.performance = normalize(payload?.performance || '');
                    await vm.handleSubmit();
                    await new Promise((resolve) => setTimeout(resolve, 700));
                    return { ok: true };
                }""",
                    {"payload": item},
                )
            except Exception:
                messages.append(f"BOSS 项目经历第 {index + 1} 条未能打开编辑表单，已跳过")
                continue
            await self._confirm_resume_risk_dialog(page)
            await page.wait_for_timeout(1000)
            if not await self._wait_editor_closed(page, "编辑项目经历"):
                messages.append(f"BOSS 项目经历第 {index + 1} 条保存失败，已保留原值")
                await self._dismiss_unsaved_resume_editor(page)
                continue
            updated_count += 1

        return {"updated": updated_count > 0, "messages": messages}

    async def _sync_resume_education_experiences(self, page, items: list[dict]) -> dict:
        if not items:
            return {"updated": False, "messages": []}

        updated_count = 0
        messages: list[str] = []
        existing_count = await page.locator('a.link-edit[ka^="user-resume-edit-eduexp"]').count()

        for index, item in enumerate(items):
            opened = await self._open_resume_editor(
                page,
                "教育经历",
                item_text=self._normalize_text(item.get("school") or ""),
                edit_selector=(
                    f'a.link-edit[ka="user-resume-edit-eduexp{index}"]'
                    if index < existing_count
                    else ".resume-education a.link-edit"
                ),
                add_selector='[ka="user-resume-add-eduexp"], .resume-education a.link-add',
            )
            if not opened:
                messages.append(f"BOSS 教育经历第 {index + 1} 条未找到编辑入口，已跳过")
                continue
            await page.wait_for_timeout(600)
            try:
                await page.evaluate(
                    """async ({ payload }) => {
                    const container = document.querySelector('.resume-education .item-form');
                    const vm = container?.__vue__;
                    const form = vm?._data?.formData || vm?.formData;
                    if (!vm || !form) {
                        throw new Error('未找到教育经历表单。');
                    }
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    form.school = normalize(payload?.school || '');
                    form.degree = String(payload?.degree_code || form.degree || '');
                    form.major = normalize(payload?.major || '');
                    form.startDate = normalize(payload?.start_year || '');
                    form.endDate = normalize(payload?.end_year || '');
                    form.eduDescription = normalize(payload?.edu_description || '');
                    form.thesisTitle = normalize(payload?.thesis_title || '');
                    form.thesisDesc = normalize(payload?.thesis_desc || '');
                    form.course = Array.isArray(payload?.course) ? payload.course.slice(0, 5).map((item) => normalize(item)).filter(Boolean) : [];
                    await vm.handleSubmit();
                    await new Promise((resolve) => setTimeout(resolve, 700));
                    return { ok: true };
                }""",
                    {"payload": item},
                )
            except Exception:
                messages.append(f"BOSS 教育经历第 {index + 1} 条未能打开编辑表单，已跳过")
                continue
            await self._confirm_resume_risk_dialog(page)
            await page.wait_for_timeout(1200)
            saved = await self._wait_editor_closed(page, "编辑教育经历")
            if not saved:
                saved = await self._finish_resume_editor(page, "编辑教育经历")
            if not saved:
                messages.append(f"BOSS 教育经历第 {index + 1} 条保存失败，已保留原值")
                await self._dismiss_unsaved_resume_editor(page)
                continue
            updated_count += 1

        return {"updated": updated_count > 0, "messages": messages}

    async def _sync_resume_certificates(self, page, certificates: list[str]) -> dict:
        targets = [item for item in certificates if str(item or "").strip()]
        if not targets:
            return {"updated": False, "messages": []}

        visible_text = await page.evaluate(
            "() => (document.body?.innerText || '').replace(/\\s+/g, ' ').trim()"
        )
        pending = [item for item in targets if item not in visible_text]
        if not pending:
            return {"updated": False, "messages": []}

        opened = await self._open_resume_editor(
            page,
            "资格证书",
            add_selector='[ka="custom_add_btn_certificationList"], .resume-certification a.link-add',
        )
        if not opened:
            return {"updated": False, "messages": ["BOSS 资格证书编辑入口未找到，已跳过"]}
        await page.wait_for_timeout(700)
        result = await page.evaluate(
            """async ({ certificates }) => {
                const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const clickIncludes = (selector, keyword) => {
                    const nodes = Array.from(document.querySelectorAll(selector));
                    const node = nodes.find((el) => normalize(el.innerText || el.textContent || '').includes(keyword) && el.offsetParent !== null);
                    if (!node) return false;
                    node.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                    return true;
                };

                let selected = 0;
                for (const item of certificates || []) {
                    if (clickIncludes('.certification-dialog label.checkbox', normalize(item))) {
                        selected += 1;
                        await new Promise((resolve) => setTimeout(resolve, 120));
                    }
                }
                clickIncludes('.certification-dialog button, .certification-dialog .btn', '确定');
                await new Promise((resolve) => setTimeout(resolve, 700));
                return { selected };
            }""",
            {"certificates": pending},
        )
        await page.wait_for_timeout(600)
        messages: list[str] = []
        selected = int(result.get("selected") or 0)
        if selected < len(pending):
            messages.append("BOSS 部分资格证书未命中可选项，已跳过")
        return {"updated": selected > 0, "messages": messages}

    async def _sync_resume_skills(self, page, skills: list[str]) -> dict:
        normalized_skills = [self._normalize_text(item) for item in skills if self._normalize_text(item)]
        if not normalized_skills:
            return {"updated": False, "messages": []}

        opened = await self._open_resume_editor(
            page,
            "专业技能",
            edit_selector='a.link-edit[ka="user-resume-edit-professional-skill"], .resume-professionalSkill a.link-edit, .resume-professional-skill a.link-edit',
            add_selector=".custom-add-item, .catalogue-item",
        )
        if not opened:
            return {"updated": False, "messages": ["BOSS 专业技能入口未找到，已跳过"]}
        skill_text = "；".join(normalized_skills[:20])[:1000]
        try:
            result = await page.evaluate(
                """({ skillText }) => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    const root = Array.from(document.querySelectorAll('*')).find((el) => {
                        const title = el.querySelector('h3');
                        return title && ['添加专业技能', '编辑专业技能'].includes(normalize(title.textContent));
                    });
                    const textarea = root?.querySelector('textarea');
                    const title = normalize(root?.querySelector('h3')?.textContent || '');
                    if (!textarea) return { ok: false, title };
                    textarea.focus();
                    textarea.value = skillText;
                    textarea.dispatchEvent(new Event('input', { bubbles: true }));
                    textarea.dispatchEvent(new Event('change', { bubbles: true }));
                    return { ok: true, title };
                }""",
                {"skillText": skill_text},
            )
        except Exception:
            await self._dismiss_unsaved_resume_editor(page)
            return {"updated": False, "messages": ["BOSS 专业技能写入失败，已跳过"]}
        title = str(result.get("title") or "添加专业技能")
        if not result.get("ok"):
            await self._dismiss_unsaved_resume_editor(page)
            return {"updated": False, "messages": ["BOSS 专业技能表单未成功打开，已跳过"]}
        saved = await self._finish_resume_editor(page, title)
        if not saved:
            return {"updated": False, "messages": ["BOSS 专业技能保存失败，已保留原值"]}
        return {"updated": True, "messages": []}

    async def _click_visible_selector(self, page, selector: str) -> bool:
        return bool(
            await page.evaluate(
                """(selector) => {
                    const node = Array.from(document.querySelectorAll(selector)).find((el) => el.offsetParent !== null);
                    if (!node) return false;
                    node.scrollIntoView({ block: 'center', inline: 'center' });
                    if (typeof node.click === 'function') {
                        node.click();
                    } else {
                        node.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                    }
                    return true;
                }""",
                selector,
            )
        )

    async def _wait_resume_ready(self, page) -> None:
        with contextlib.suppress(Exception):
            await page.wait_for_function(
                """() => {
                    const text = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
                    return text.includes('我的在线简历') && text.includes('个人优势') && text.includes('期望职位');
                }""",
                timeout=12000,
            )
        await page.wait_for_timeout(500)

    async def _open_resume_editor(
        self,
        page,
        section_name: str,
        *,
        item_text: str = "",
        edit_selector: str = "",
        add_selector: str = "",
    ) -> bool:
        await self._dismiss_unsaved_resume_editor(page)
        return bool(
            await page.evaluate(
                """({ sectionName, itemText, editSelector, addSelector }) => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    const clickNode = (node) => {
                        if (!node) return false;
                        if (node.offsetParent !== null && typeof node.scrollIntoView === 'function') {
                            node.scrollIntoView({ block: 'center', inline: 'center' });
                        }
                        node.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                        node.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                        if (typeof node.click === 'function') {
                            node.click();
                        } else {
                            node.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                        }
                        return true;
                    };
                    const visibleNode = (selector) => {
                        if (!selector) return null;
                        return Array.from(document.querySelectorAll(selector)).find((el) => el.offsetParent !== null) || null;
                    };
                    const anyNode = (selector) => {
                        if (!selector) return null;
                        return document.querySelector(selector);
                    };
                    const findSectionRoot = (name) =>
                        Array.from(document.querySelectorAll('.resume-item, .custom-add-item, .catalogue-item')).find((el) => {
                            const text = normalize(el.textContent || '');
                            return text && (text.startsWith(name) || text.includes(name));
                        }) || null;
                    if (itemText) {
                        const item = Array.from(document.querySelectorAll('li, .expect-list-item, .custom-add-item, .resume-item')).find((el) => {
                            const text = normalize(el.textContent || '');
                            return text && text.includes(itemText);
                        });
                        const itemEdit = item?.querySelector('a.link-edit, .link-edit');
                        if (clickNode(itemEdit)) return true;
                        if (clickNode(item)) return true;
                    }
                    if (clickNode(visibleNode(editSelector))) return true;
                    if (clickNode(anyNode(editSelector))) return true;
                    const sectionRoot = findSectionRoot(sectionName);
                    const sectionEdit = sectionRoot?.querySelector('a.link-edit, .link-edit');
                    if (clickNode(sectionEdit)) return true;
                    if (sectionName === '专业技能') {
                        const skillCard = Array.from(document.querySelectorAll('.resume-professionalSkill, .resume-professional-skill, .custom-add-item, .catalogue-item, span')).find((el) => {
                            const text = normalize(el.textContent || '');
                            return text === '专业技能突出专业技能、特长展示' || text === '专业技能 突出专业技能、特长展示' || text.includes('专业技能');
                        });
                        const skillTarget = skillCard?.closest?.('.resume-professionalSkill, .resume-professional-skill, .custom-add-item, .catalogue-item') || skillCard;
                        if (clickNode(skillTarget)) return true;
                    }
                    if (clickNode(visibleNode(addSelector))) return true;
                    if (clickNode(anyNode(addSelector))) return true;
                    const addLink = Array.from(document.querySelectorAll('a.link-add, .link-add')).find((el) => {
                        const root = el.closest('.resume-item');
                        return normalize(root?.textContent || '').startsWith(sectionName);
                    });
                    return clickNode(addLink);
                }""",
                {
                    "sectionName": section_name,
                    "itemText": self._normalize_text(item_text),
                    "editSelector": edit_selector,
                    "addSelector": add_selector,
                },
            )
        )

    async def _dismiss_unsaved_resume_editor(self, page) -> bool:
        dismissed = bool(
            await page.evaluate(
                """() => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    const dialog = Array.from(document.querySelectorAll('*')).find((el) => normalize(el.textContent).includes('有内容没有保存，确定退出编辑吗？'));
                    if (!dialog) return false;
                    const confirm = Array.from(document.querySelectorAll('button, span, a')).find((el) => el.offsetParent !== null && normalize(el.textContent) === '确定');
                    if (!confirm) return false;
                    if (typeof confirm.click === 'function') {
                        confirm.click();
                    } else {
                        confirm.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                    }
                    return true;
                }"""
            )
        )
        if dismissed:
            await page.wait_for_timeout(600)
        return dismissed

    async def _has_resume_error_dialog(self, page) -> bool:
        return bool(
            await page.evaluate(
                """() => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    const isVisible = (node) => !!node && node.offsetParent !== null;
                    return Array.from(document.querySelectorAll('*')).some((el) => {
                        if (!isVisible(el)) return false;
                        const text = normalize(el.textContent || '');
                        if (!text) return false;
                        return text.includes('提交失败') || text.includes('请填写正确的薪资');
                    });
                }"""
            )
        )

    async def _finish_resume_editor(self, page, title: str) -> bool:
        clicked = bool(
            await page.evaluate(
                """({ title }) => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    const root = Array.from(document.querySelectorAll('*')).find((el) => {
                        const h3 = el.querySelector('h3');
                        return h3 && normalize(h3.textContent) === title;
                    });
                    const button = Array.from(root?.querySelectorAll('button') || []).find((el) => normalize(el.textContent).includes('完成') && !el.disabled);
                    if (!button) return false;
                    button.click();
                    return true;
                }""",
                {"title": title},
            )
        )
        if not clicked:
            return False
        await self._confirm_resume_risk_dialog(page)
        await self._confirm_resume_modify_dialog(page)
        await page.wait_for_timeout(1200)
        if await self._has_resume_error_dialog(page):
            return False
        return await self._wait_editor_closed(page, title)

    async def _wait_editor_closed(self, page, title: str) -> bool:
        for _ in range(10):
            is_open = bool(
                await page.evaluate(
                    """({ title }) => {
                        const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                        return Array.from(document.querySelectorAll('h3')).some((el) => {
                            if (el.offsetParent === null) return false;
                            return normalize(el.textContent) === title;
                        });
                    }""",
                    {"title": title},
                )
            )
            if not is_open:
                return True
            await page.wait_for_timeout(500)
        return False

    async def _confirm_resume_risk_dialog(self, page) -> bool:
        confirmed = bool(
            await page.evaluate(
                """() => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    const nodes = Array.from(document.querySelectorAll('.risk-dialog .btn.btn-primary, .risk-dialog .btn-sure, .risk-dialog .btn-primary, .risk-dialog button, .risk-dialog span, button, span'));
                    const node = nodes.find((el) => {
                        if (el.offsetParent === null) return false;
                        const text = normalize(el.textContent);
                        return text === '确认提交' || text === '确定';
                    });
                    if (!node) return false;
                    if (typeof node.click === 'function') {
                        node.click();
                    } else {
                        node.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                    }
                    return true;
                }"""
            )
        )
        if confirmed:
            await page.wait_for_timeout(1200)
        return confirmed

    async def _confirm_resume_modify_dialog(self, page) -> bool:
        confirmed = bool(
            await page.evaluate(
                """() => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    const isVisible = (node) => !!node && node.offsetParent !== null;
                    const dialog = Array.from(document.querySelectorAll('*')).find((el) => {
                        if (!isVisible(el)) return false;
                        const text = normalize(el.textContent || '');
                        return text.includes('确认修改') || text.includes('确定要修改吗');
                    });
                    if (!dialog) return false;
                    const confirm = Array.from(dialog.querySelectorAll('button, span, a')).find((el) => {
                        if (!isVisible(el)) return false;
                        return normalize(el.textContent) === '确定';
                    });
                    if (!confirm) return false;
                    if (typeof confirm.click === 'function') {
                        confirm.click();
                    } else {
                        confirm.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                    }
                    return true;
                }"""
            )
        )
        if confirmed:
            await page.wait_for_timeout(1200)
        return confirmed

    async def _reload_resume_page(self, page, target_url: str) -> None:
        with contextlib.suppress(Exception):
            await page.goto(target_url, wait_until="domcontentloaded")
            await self._wait_resume_ready(page)

    async def _import_resume_snapshot(
        self,
        profile_dir: str,
        *,
        headless: bool | None = None,
        target_url: str = "https://www.zhipin.com/web/geek/resume",
    ) -> dict:
        from playwright.async_api import async_playwright

        launch_headless = settings.browser_headless if headless is None else headless
        use_persistent_context = bool(launch_headless and not settings.browser_fake_headless)

        async with async_playwright() as playwright:
            browser = None
            chrome_pid: int | None = None
            if use_persistent_context:
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    **build_browser_launch_options(launch_headless),
                )
            else:
                chrome_path = self._resolve_chrome_executable()
                profile_path = Path(profile_dir)
                if not profile_path.is_absolute():
                    profile_path = Path.cwd() / profile_path
                profile_path.mkdir(parents=True, exist_ok=True)

                debug_port = self._pick_free_port()
                chrome_pid = self._launch_external_chrome(
                    chrome_path,
                    profile_path,
                    debug_port,
                    target_url,
                    launch_options=build_browser_launch_options(launch_headless),
                )
                ws_endpoint = self._wait_for_cdp_endpoint(debug_port, timeout=20, should_stop=lambda: False)
                if not ws_endpoint:
                    raise RuntimeError("BOSS 简历浏览器已启动，但未能连接到调试端口。")
                browser = await playwright.chromium.connect_over_cdp(ws_endpoint)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()

            await context.add_init_script(STEALTH_INIT_SCRIPT)
            await context.add_init_script(LAODENG_INIT_SCRIPT)
            page = context.pages[0] if context.pages else await context.new_page()

            try:
                await page.goto(target_url, wait_until="domcontentloaded")
                with contextlib.suppress(Exception):
                    await page.wait_for_function(
                        """() => {
                            const text = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
                            return text.includes('我的在线简历') || text.includes('个人优势') || text.includes('期望职位');
                        }""",
                        timeout=12000,
                    )
                with contextlib.suppress(Exception):
                    await page.wait_for_timeout(800)
                current_url = (page.url or "").lower()
                if "login" in current_url or "passport" in current_url:
                    raise RuntimeError("BOSS 当前未登录，请先在账号配置浏览器中完成登录后再导入。")
                lines = await page.evaluate(
                    "() => document.body.innerText.split('\\n').map(line => line.trim()).filter(Boolean)"
                )
                return self._parse_resume_lines(lines, page.url)
            finally:
                with contextlib.suppress(Exception):
                    await context.close()
                if browser is not None:
                    with contextlib.suppress(Exception):
                        await browser.close()
                self._terminate_chrome_process(chrome_pid)

    @staticmethod
    def _parse_resume_lines(lines: list[str], source_url: str) -> dict:
        normalized_lines = [re.sub(r"\s+", " ", str(line or "")).strip() for line in lines if str(line or "").strip()]

        def find_index(candidates: list[str], start_at: int = 0) -> int | None:
            for idx in range(start_at, len(normalized_lines)):
                if normalized_lines[idx] in candidates:
                    return idx
            return None

        def section_slice(names: list[str], next_names: list[str]) -> list[str]:
            start_idx = find_index(names, content_start)
            if start_idx is None:
                return []
            start = start_idx + 1
            end = len(normalized_lines)
            for next_name in next_names:
                next_idx = find_index([next_name], start)
                if next_idx is not None:
                    end = min(end, next_idx)
            return normalized_lines[start:end]

        def is_date_line(value: str) -> bool:
            return bool(re.search(r"\d{4}[./-]\d{1,2}\s*-\s*(?:\d{4}[./-]\d{1,2}|至今)", value))

        def is_contact_line(value: str) -> bool:
            return bool(
                re.search(r"\d{3,}\*+\d+|@|[A-Za-z]{2,}\*+[A-Za-z0-9]*|微信|邮箱|电话", value)
            )

        name = ""
        contact_summary = ""
        headline = ""
        profile_anchor = find_index(["我的在线简历"])
        content_start = profile_anchor or 0
        if profile_anchor is not None:
            skip_tokens = {
                "新头像正在审核中",
                "预览",
                "导入已有简历",
                "导出为附件简历",
                "简历专业评分",
                "个人优势",
                "期望职位",
                "工作经历",
                "工作/实习经历",
                "项目经历",
                "教育经历",
                "资格证书",
                "附件管理",
            }
            head_candidates = normalized_lines[profile_anchor + 1 : profile_anchor + 18]
            summary_mode = False
            profile_tokens: list[str] = []
            for token in head_candidates:
                if token == "简历专业评分":
                    summary_mode = True
                    continue
                if summary_mode and (token.endswith("分") or "待优化项" in token):
                    continue
                if token in skip_tokens:
                    continue
                profile_tokens.append(token)

            if profile_tokens:
                name = profile_tokens[0]
                headline_tokens: list[str] = []
                contact_tokens: list[str] = []
                for token in profile_tokens[1:]:
                    if is_contact_line(token):
                        contact_tokens.append(token)
                    elif not contact_tokens:
                        headline_tokens.append(token)
                headline = " / ".join(headline_tokens[:2]).strip()
                contact_summary = " ".join(contact_tokens[:3]).strip()

        if not name:
            for idx, value in enumerate(normalized_lines):
                if value == "我的在线简历" and idx + 3 < len(normalized_lines):
                    name = normalized_lines[idx + 1]
                    break

        summary_lines = section_slice(["个人优势"], ["期望职位"])
        preference_lines = section_slice(["期望职位"], ["工作经历", "工作/实习经历"])
        work_lines = section_slice(["工作经历", "工作/实习经历"], ["项目经历"])
        project_lines = section_slice(["项目经历"], ["教育经历"])
        education_lines = section_slice(["教育经历"], ["资格证书", "自定义添加"])
        certificate_lines = section_slice(["资格证书"], ["社团/组织经历", "自定义添加", "附件管理"])
        attachment_lines = section_slice(["附件管理"], ["隐私设置", "道具"])

        preference_items = [item for item in preference_lines if item and item not in {"全职职位", "兼职职位", "实习职位"}]
        job_type = ""
        with contextlib.suppress(IndexError):
            if preference_lines and preference_lines[0].endswith("职位"):
                job_type = preference_lines[0]
            elif preference_lines:
                job_type = preference_lines[0]

        preferred_roles: list[str] = []
        preferred_salary = ""
        preferred_locations = ""
        for item in preference_items:
            match = re.match(r"^(.*?)(\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?[KkWw])(.+)$", item)
            if match:
                role = match.group(1).strip()
                salary = match.group(2).strip().upper()
                location = match.group(3).strip()
                if role:
                    preferred_roles.append(role)
                if not preferred_salary:
                    preferred_salary = salary
                if location and location not in preferred_locations:
                    preferred_locations = f"{preferred_locations}，{location}".strip("，")
            elif item not in preferred_roles:
                preferred_roles.append(item)

        wechat_id = ""
        wechat_match = re.search(r"([A-Za-z]{2,}\*+[A-Za-z0-9]*)", contact_summary)
        if wechat_match:
            wechat_id = wechat_match.group(1)

        job_preferences = {
            "job_type": job_type,
            "desired_role": " / ".join(preferred_roles),
            "salary": preferred_salary,
            "locations": preferred_locations,
            "raw_tokens": preference_lines,
        }

        work_experiences: list[dict] = []
        idx = 0
        while idx < len(work_lines):
            if idx + 3 >= len(work_lines):
                break
            company = work_lines[idx]
            title = work_lines[idx + 1]
            employment_type = work_lines[idx + 2]
            date_range = work_lines[idx + 3] if idx + 3 < len(work_lines) else ""
            if not is_date_line(date_range):
                idx += 1
                continue
            idx += 4
            tags: list[str] = []
            description_parts: list[str] = []
            while idx < len(work_lines):
                current = work_lines[idx]
                if idx + 3 < len(work_lines) and is_date_line(work_lines[idx + 3]):
                    break
                if current in {"内容：", "业绩："}:
                    idx += 1
                    continue
                if len(current) <= 20 and "\n" not in current and "：" not in current and not current.startswith("项目背景"):
                    tags.append(current)
                else:
                    description_parts.append(current)
                idx += 1
            work_experiences.append(
                {
                    "company": company,
                    "title": title,
                    "employment_type": employment_type,
                    "date_range": date_range,
                    "tags": tags,
                    "description": "\n".join(description_parts).strip(),
                }
            )

        project_experiences: list[dict] = []
        idx = 0
        while idx + 2 < len(project_lines):
            name_line = project_lines[idx]
            role = project_lines[idx + 1]
            date_range = project_lines[idx + 2]
            if not is_date_line(date_range):
                idx += 1
                continue
            idx += 3
            description_parts: list[str] = []
            while idx < len(project_lines):
                current = project_lines[idx]
                if idx + 2 < len(project_lines) and is_date_line(project_lines[idx + 2]):
                    break
                if current == "内容：":
                    idx += 1
                    continue
                description_parts.append(current)
                idx += 1
            project_experiences.append(
                {
                    "name": name_line,
                    "role": role,
                    "date_range": date_range,
                    "description": "\n".join(description_parts).strip(),
                }
            )

        education_experiences: list[dict] = []
        if len(education_lines) >= 4:
            education_experiences.append(
                {
                    "school": education_lines[0],
                    "date_range": education_lines[1],
                    "major": education_lines[2],
                    "degree": education_lines[3],
                    "description": "\n".join(education_lines[4:]).strip() if len(education_lines) > 4 else "",
                }
            )

        attachments: list[dict] = []
        for idx, value in enumerate(attachment_lines):
            if value.startswith("文件（") or value == "附件简历":
                continue
            if value.endswith(".pdf") or value.endswith(".doc") or value.endswith(".docx"):
                meta = attachment_lines[idx + 1] if idx + 1 < len(attachment_lines) else ""
                attachments.append({"name": value, "meta": meta})

        return {
            "source_url": source_url,
            "full_name": name,
            "headline": headline,
            "contact_summary": contact_summary,
            "wechat_id": wechat_id,
            "summary": "\n".join(summary_lines).strip(),
            "job_preferences": job_preferences,
            "work_experiences": work_experiences,
            "project_experiences": project_experiences,
            "education_experiences": education_experiences,
            "certificates": [
                item
                for item in certificate_lines
                if item and "推荐" not in item and "含金量" not in item and "证明你的专业程度" not in item
            ],
            "skills": [],
            "languages": [],
            "attachments": attachments,
            "raw_snapshot": {"lines": normalized_lines},
        }

    @staticmethod
    def _split_resume_terms(value: str | None) -> list[str]:
        if not value:
            return []
        raw_items = re.split(r"[/|,，、；;\n]+", str(value))
        results: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            normalized = re.sub(r"\s+", " ", item).strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            results.append(normalized)
        return results

    @classmethod
    def _build_resume_sync_payload(cls, master_resume: dict) -> dict:
        job_preferences = dict(master_resume.get("job_preferences", {}) or {})
        salary_text = str(job_preferences.get("salary") or "").strip()
        salary_min, salary_max = cls.normalize_salary(salary_text) if salary_text else (None, None)
        salary_min_k = int(round(salary_min)) if salary_min is not None else None
        salary_max_k = int(round(salary_max)) if salary_max is not None else None
        desired_role = str(job_preferences.get("desired_role") or "").strip()
        email = cls._extract_resume_email(master_resume)
        return {
            "basic_info": {
                "full_name": str(master_resume.get("full_name") or "").strip(),
                "wechat_id": str(master_resume.get("wechat_id") or "").strip(),
                "email": email,
            },
            "summary": str(master_resume.get("summary") or "").strip(),
            "desired_role": desired_role,
            "roles": cls._split_resume_terms(job_preferences.get("desired_role")),
            "locations_text": str(job_preferences.get("locations") or "").strip(),
            "locations": cls._split_resume_terms(job_preferences.get("locations")),
            "salary_text": salary_text,
            "salary_min_k": salary_min_k,
            "salary_max_k": salary_max_k,
            "work_experiences": [
                cls._build_work_experience_sync_payload(item, desired_role)
                for item in (master_resume.get("work_experiences") or [])
                if isinstance(item, dict)
            ],
            "project_experiences": [
                cls._build_project_experience_sync_payload(item, desired_role)
                for item in (master_resume.get("project_experiences") or [])
                if isinstance(item, dict)
            ],
            "education_experiences": [
                cls._build_education_experience_sync_payload(item)
                for item in (master_resume.get("education_experiences") or [])
                if isinstance(item, dict)
            ],
            "certificates": [str(item).strip() for item in (master_resume.get("certificates") or []) if str(item).strip()],
            "skills": [str(item).strip() for item in (master_resume.get("skills") or []) if str(item).strip()],
        }

    @classmethod
    def _build_work_experience_sync_payload(cls, item: dict, desired_role: str) -> dict:
        start_date, end_date = cls._parse_date_range(item.get("date_range"), year_only=False)
        description = str(item.get("description") or "").strip()
        tags = [str(tag).strip() for tag in (item.get("tags") or []) if str(tag).strip()]
        title = str(item.get("title") or "").strip()
        return {
            "company": str(item.get("company") or "").strip(),
            "title": title or desired_role,
            "role": title or desired_role,
            "department": str(item.get("department") or "").strip(),
            "industry": str(item.get("industry") or "计算机软件").strip() or "计算机软件",
            "start_date": start_date,
            "end_date": end_date,
            "work_content": description[:3000],
            "work_performance": cls._summarize_first_lines(description, max_length=900),
            "tags": tags[:6],
            "is_intern": "实习" in str(item.get("employment_type") or ""),
        }

    @classmethod
    def _build_project_experience_sync_payload(cls, item: dict, desired_role: str) -> dict:
        start_date, end_date = cls._parse_date_range(item.get("date_range"), year_only=False)
        description = str(item.get("description") or "").strip()
        role = str(item.get("role") or "").strip() or str(item.get("title") or "").strip() or desired_role.strip()
        return {
            "name": str(item.get("name") or "").strip(),
            "role": role,
            "url": str(item.get("url") or "").strip(),
            "start_date": start_date,
            "end_date": end_date,
            "description": description[:3000],
            "performance": cls._summarize_first_lines(description, max_length=900),
        }

    @classmethod
    def _build_education_experience_sync_payload(cls, item: dict) -> dict:
        start_year, end_year = cls._parse_date_range(item.get("date_range"), year_only=True)
        description = str(item.get("description") or "").strip()
        degree_text = str(item.get("degree") or "").strip()
        return {
            "school": str(item.get("school") or "").strip(),
            "major": str(item.get("major") or "").strip(),
            "degree": degree_text,
            "degree_code": cls._map_degree_code(degree_text),
            "start_year": start_year,
            "end_year": end_year,
            "edu_description": description[:3000],
            "thesis_title": str(item.get("thesis_title") or "").strip(),
            "thesis_desc": str(item.get("thesis_desc") or "").strip()[:600],
            "course": [str(course).strip() for course in (item.get("course") or []) if str(course).strip()][:5],
        }

    @staticmethod
    def _parse_date_range(value: str | None, *, year_only: bool) -> tuple[str, str]:
        text = str(value or "")
        parts = re.split(r"\s*-\s*", text)
        start = BossAdapter._normalize_resume_date(parts[0] if parts else "", year_only=year_only)
        end = BossAdapter._normalize_resume_date(parts[1] if len(parts) > 1 else "", year_only=year_only)
        return start, end

    @staticmethod
    def _normalize_resume_date(value: str, *, year_only: bool) -> str:
        normalized = str(value or "").strip().replace("/", ".").replace("-", ".")
        match = re.search(r"(\d{4})(?:\.(\d{1,2}))?", normalized)
        if not match:
            return ""
        year = match.group(1)
        month = match.group(2)
        if year_only:
            return year
        if not month:
            return f"{year}-01"
        return f"{year}-{int(month):02d}"

    @staticmethod
    def _map_degree_code(value: str) -> str:
        mapping = {
            "初中及以下": "209",
            "中专": "208",
            "中技": "208",
            "高中": "206",
            "大专": "202",
            "本科": "203",
            "硕士": "204",
            "博士": "205",
        }
        return mapping.get(str(value or "").strip(), "203")

    @staticmethod
    def _summarize_first_lines(value: str, *, max_length: int) -> str:
        lines = [re.sub(r"\s+", " ", line).strip("•\t -") for line in str(value or "").splitlines()]
        merged = "；".join(line for line in lines if line)
        return merged[:max_length].strip()

    @staticmethod
    def _extract_resume_email(master_resume: dict) -> str:
        candidates = [
            master_resume.get("email"),
            master_resume.get("contact_summary"),
        ]
        for candidate in candidates:
            match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", str(candidate or ""))
            if match:
                return match.group(0).strip()
        return ""
