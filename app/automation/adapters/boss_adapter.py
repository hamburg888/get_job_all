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
        salary = BossAdapter._decode_boss_salary_text(raw_salary).replace("元", "").replace("万", "w").replace("K", "k").strip()
        if "-" not in salary:
            value = BossAdapter._parse_salary_part(salary)
            return value, value
        left, right = salary.split("-", maxsplit=1)
        left, right = BossAdapter._inherit_range_units(left, right)
        return BossAdapter._parse_salary_part(left), BossAdapter._parse_salary_part(right)

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
