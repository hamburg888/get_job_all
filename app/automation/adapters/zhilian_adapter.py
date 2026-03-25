import asyncio
import contextlib
import json
import logging
import re
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import urllib.request
from urllib.parse import urlparse

from app.core.config import settings
from app.automation.browser_factory import (
    LAODENG_INIT_SCRIPT,
    STEALTH_INIT_SCRIPT,
    build_browser_launch_options,
)
from app.services.live_preview_service import live_preview_service

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ZhilianSelectors:
    login_link: str = "a.header-nav__c-no-login"
    keyword_input: str = 'input.query-search__content-input[placeholder="输入职位、公司等搜索"]'
    search_button: str = "button.query-search__content-button"
    job_list: str = "div.positionlist__list"
    job_card: str = "div.joblist-box__item.clearfix"
    salary: str = "span.summary-plane__salary"
    title: str = "h1.summary-plane__title"
    summary_info_items: str = "ul.summary-plane__info > li"
    description: str = "div.describtion__detail-content"
    company: str = "a.company__title"
    tag_items: str = "div.describtion__skills span.describtion__skills-item"
    action_container: str = "div.summary-plane__action"
    deliver_button: str = 'div.summary-plane__action button:has-text("立即投递")'
    delivered_button: str = 'div.summary-plane__action button:has-text("已投递")'
    success_dialog_main: str = "span.deliver-title__left__word"
    success_dialog_secondary: str = "span.deliver-dialog__box__title"
    daily_limit_message: str = "div.a-job-apply-workflow div.a-job-apply-error-message-panel__title"
    next_page: str = 'a.btn.soupager__btn:has-text("下一页")'


class ZhilianDailyLimitReached(RuntimeError):
    """Raised when Zhilian shows the daily delivery limit popup."""


class ZhilianAdapter:
    def __init__(self) -> None:
        self.selectors = ZhilianSelectors()
        self._runtime_lock = threading.Lock()
        self._active_loop = None
        self._active_context = None
        self._active_browser = None
        self._active_chrome_pid: int | None = None

    @staticmethod
    def extract_job_code(detail_url: str) -> str:
        match = re.search(r"/jobdetail/([A-Z0-9]+)\.htm", detail_url)
        return match.group(1) if match else urlparse(detail_url).path.rsplit("/", maxsplit=1)[-1]

    @staticmethod
    def normalize_salary(raw_salary: str) -> tuple[float | None, float | None]:
        salary = raw_salary.replace("元", "").replace("万", "w").replace("K", "k").strip()
        if "-" not in salary:
            value = ZhilianAdapter._parse_salary_part(salary)
            return value, value
        left, right = salary.split("-", maxsplit=1)
        left, right = ZhilianAdapter._inherit_range_units(left, right)

        return ZhilianAdapter._parse_salary_part(left), ZhilianAdapter._parse_salary_part(right)

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

    async def run_search_flow(
        self,
        search_terms: list[str],
        profile_dir: str,
        per_job_callback: Callable[[dict], None],
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None = None,
        confirm_login_callback: Callable[[], bool] | None = None,
        *,
        entry_url: str | None = None,
        task_run_id: int | None = None,
        headless: bool | None = None,
        live_preview_enabled: bool = False,
        live_preview_interval_ms: int = 1000,
        delivery_interval_seconds: int = 3,
        max_deliveries_per_run: int = 100,
    ) -> None:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright

        launch_headless = settings.browser_headless if headless is None else headless
        launch_mode = (
            "fake_headless" if launch_headless and settings.browser_fake_headless else "headless" if launch_headless else "visible"
        )
        logger.info(
            "Zhilian launch mode resolved: mode=%s requested_headless=%s settings_headless=%s fake_headless=%s",
            launch_mode,
            headless,
            settings.browser_headless,
            settings.browser_fake_headless,
        )
        use_persistent_context = bool(launch_headless and not settings.browser_fake_headless)
        target_entry_url = (entry_url or settings.zhilian_entry_url).strip() or settings.zhilian_entry_url
        use_manual_entry_url = bool(entry_url and entry_url.strip())
        effective_search_terms = search_terms if search_terms else [""]
        async with async_playwright() as playwright:
            browser = None
            chrome_pid: int | None = None
            if use_persistent_context:
                launch_options = build_browser_launch_options(launch_headless)
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
                    target_entry_url,
                    launch_options=build_browser_launch_options(launch_headless),
                )
                self._bind_runtime(asyncio.get_running_loop(), None, None, chrome_pid)
                ws_endpoint = self._wait_for_cdp_endpoint(debug_port, timeout=20, should_stop=should_stop)
                if not ws_endpoint:
                    raise RuntimeError("智联外部 Chrome 已启动，但未能连接到调试端口。")
                browser = await playwright.chromium.connect_over_cdp(ws_endpoint)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
            self._bind_runtime(asyncio.get_running_loop(), context, browser, chrome_pid)
            await context.add_init_script(STEALTH_INIT_SCRIPT)
            await context.add_init_script(LAODENG_INIT_SCRIPT)
            list_page = context.pages[0] if context.pages else await context.new_page()
            preview_ref = {"page": list_page}
            preview_task = None
            if launch_headless and live_preview_enabled and task_run_id is not None:
                preview_task = asyncio.create_task(
                    self._preview_loop(
                        context,
                        preview_ref,
                        should_stop,
                        task_run_id,
                        live_preview_interval_ms,
                    )
                )
            await list_page.goto(target_entry_url, wait_until="domcontentloaded")
            try:
                if await self.is_login_required(list_page):
                    if status_callback:
                        status_callback("智联招聘当前未登录，请先在浏览器中完成登录，然后回到前端点击确认已登录。")
                    await self.wait_for_login(list_page, should_stop, status_callback, confirm_login_callback)
                    if should_stop():
                        return
                for search_term in effective_search_terms:
                    search_label = search_term or "手动URL"
                    if should_stop():
                        break
                    if status_callback and use_manual_entry_url:
                        status_callback("正在打开手动配置的筛选 URL")
                    elif status_callback:
                        status_callback(f"正在搜索关键词: {search_label}")
                    preview_ref["page"] = list_page
                    await list_page.goto(target_entry_url, wait_until="domcontentloaded")
                    if use_manual_entry_url:
                        if await self._sleep_or_stop(6.0, should_stop):
                            break
                    else:
                        await list_page.locator(self.selectors.keyword_input).fill("")
                        await list_page.locator(self.selectors.keyword_input).fill(search_term)
                        await list_page.locator(self.selectors.search_button).click()
                        if await self._sleep_or_stop(10.0, should_stop):
                            break

                    while not should_stop():
                        list_page = await self.ensure_list_page(context, list_page, target_entry_url)
                        cards = list_page.locator(self.selectors.job_card)
                        count = await cards.count()
                        if count == 0:
                            logger.warning("No job cards found on current page.")
                            if status_callback:
                                status_callback(f"搜索词 {search_term} 当前页未找到岗位卡片，切换下一个搜索词。")
                            break
                        for index in range(count):
                            if should_stop():
                                break
                            if status_callback:
                                status_callback(f"搜索词 {search_label}：正在打开第 {index + 1} 个岗位详情")
                            list_page = await self.ensure_list_page(context, list_page, target_entry_url)
                            pages_before_detail = self._snapshot_open_pages(context)
                            detail_page = await self.open_detail_page(context, list_page, cards.nth(index))
                            preview_ref["page"] = detail_page
                            stop_after_cleanup = False
                            stop_due_to_daily_limit = False
                            daily_limit_reason = ""
                            job_payload = None
                            detail_url = self._safe_page_url(detail_page)
                            try:
                                await detail_page.wait_for_load_state("domcontentloaded")
                                if await self._sleep_or_stop(2.5, should_stop):
                                    stop_after_cleanup = True
                                    continue
                                detail_url = detail_page.url
                                job_payload = await self.extract_job_payload(detail_page, detail_url)
                                job_payload["search_term"] = search_label
                                job_payload["already_delivered"] = False
                                delivery_result = await self.try_deliver(detail_page, context, should_stop)
                                job_payload["already_delivered"] = bool(delivery_result.get("already_delivered", False))
                                job_payload["delivery_success"] = bool(delivery_result.get("success", False)) and not job_payload["already_delivered"]
                                job_payload["status_reason"] = str(delivery_result.get("reason", "未完成投递"))
                                stop_due_to_daily_limit = bool(delivery_result.get("daily_limit_reached", False))
                                daily_limit_reason = job_payload["status_reason"]
                                if stop_due_to_daily_limit and status_callback:
                                    status_callback("检测到智联今日投递已超过上限，结束本次任务")
                            except (PlaywrightTimeoutError, TimeoutError):
                                logger.info("Deliver button not clickable for %s", detail_url)
                                if job_payload is not None:
                                    job_payload["already_delivered"] = False
                                    job_payload["delivery_success"] = False
                                    job_payload["status_reason"] = "未找到可点击的立即投递按钮"
                            finally:
                                if job_payload is not None:
                                    per_job_callback(job_payload)
                                list_page = await self.close_delivery_pages(
                                    context,
                                    list_page,
                                    detail_page,
                                    pages_before_detail,
                                    target_entry_url,
                                    should_stop,
                                )
                                with contextlib.suppress(Exception):
                                    await list_page.bring_to_front()
                                preview_ref["page"] = list_page
                            if stop_due_to_daily_limit:
                                raise ZhilianDailyLimitReached(daily_limit_reason or "检测到智联今日投递已超过上限，结束本次任务")
                            if stop_after_cleanup:
                                break

                        next_page = list_page.locator(self.selectors.next_page)
                        if await next_page.count() == 0:
                            break
                        if status_callback:
                            status_callback(f"搜索词 {search_label} 当前页处理完成，准备进入下一页")
                        await next_page.click()
                        if await self._sleep_or_stop(4.0, should_stop):
                            break
            finally:
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
                page = context.pages[0]
                preview_ref["page"] = page
            if page is not None and not page.is_closed():
                with contextlib.suppress(Exception):
                    await live_preview_service.capture_page("zhilian", task_run_id, page)
            await asyncio.sleep(interval)

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
        logger.info("Started external Chrome for Zhilian task: pid=%s port=%s", pid_text, debug_port)
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

    async def is_login_required(self, page) -> bool:
        login_link = page.locator(self.selectors.login_link)
        if await login_link.count() == 0:
            return False
        href = await login_link.first.get_attribute("href")
        return bool(href and "passport.zhaopin.com/login" in href)

    async def open_detail_page(self, context, list_page, card_locator):
        with contextlib.suppress(Exception):
            await list_page.bring_to_front()
        with contextlib.suppress(Exception):
            await card_locator.scroll_into_view_if_needed()

        detail_href = await self._extract_detail_href(card_locator)
        if detail_href:
            detail_page = await context.new_page()
            await detail_page.goto(detail_href, wait_until="domcontentloaded")
            return detail_page

        async with context.expect_page() as page_info:
            await card_locator.click()
        return await page_info.value

    async def ensure_list_page(self, context, current_page, target_entry_url: str):
        if current_page is not None and not current_page.is_closed():
            return current_page

        list_pages = await self._collect_list_pages(context, target_entry_url)
        if list_pages:
            logger.info(
                "Zhilian recovered list page from existing tabs: %s",
                self._safe_page_url(list_pages[0]),
            )
            return list_pages[0]

        raise RuntimeError("智联岗位列表页已被关闭，无法继续执行")

    async def _collect_list_pages(self, context, target_entry_url: str) -> list:
        candidates = []
        for page in list(context.pages):
            if page.is_closed():
                continue
            if await self._is_probable_list_page(page, target_entry_url):
                candidates.append(page)
        return candidates

    async def _is_probable_list_page(self, page, target_entry_url: str) -> bool:
        page_url = self._safe_page_url(page)
        fallback_prefixes = (
            target_entry_url,
            settings.zhilian_entry_url,
            "https://www.zhaopin.com/sou/",
            "https://sou.zhaopin.com/",
        )
        if any(page_url.startswith(prefix) for prefix in fallback_prefixes if prefix):
            return True
        with contextlib.suppress(Exception):
            if await page.locator(self.selectors.job_card).count():
                return True
        return False

    async def _extract_detail_href(self, card_locator) -> str:
        with contextlib.suppress(Exception):
            href = await card_locator.evaluate(
                """(el) => {
                    const anchor = el.querySelector('a[href*="/jobdetail/"], a[href*="jobdetail"]');
                    return anchor ? anchor.href : '';
                }"""
            )
            if href:
                return str(href)
        return ""

    async def detect_delivery_success(self, context, detail_page) -> dict:
        delivered_button = detail_page.locator(self.selectors.delivered_button)
        if await delivered_button.count():
            return {"success": True, "reason": "成功检测：详情页投递按钮已变更为已投递"}

        for page in context.pages:
            if page.is_closed():
                continue
            daily_limit = page.locator(self.selectors.daily_limit_message)
            if await daily_limit.count():
                message = (await daily_limit.first.inner_text()).strip()
                if "今日投递已超过上限" in message:
                    return {
                        "success": False,
                        "daily_limit_reached": True,
                        "reason": f"检测到投递上限弹框：{message}",
                    }

            main_success = page.locator(self.selectors.success_dialog_main)
            if await main_success.count() and "投递成功" in await main_success.first.inner_text():
                return {"success": True, "reason": "成功检测：检测到投递成功新页主提示"}

            secondary_success = page.locator(self.selectors.success_dialog_secondary)
            if await secondary_success.count() and "投递成功" in await secondary_success.first.inner_text():
                return {"success": True, "reason": "成功检测：检测到投递成功新页副提示"}

        button_snapshot = await self.inspect_deliver_button(detail_page)
        return {"success": False, "reason": f"未检测到投递成功标识 | {button_snapshot}"}

    async def try_deliver(self, detail_page, context, should_stop: Callable[[], bool]) -> dict:
        delivered_button = detail_page.locator(self.selectors.delivered_button)
        if await delivered_button.count():
            return {
                "success": False,
                "already_delivered": True,
                "reason": "点击前检测：进入详情时岗位已是已投递状态，直接跳过",
            }

        await detail_page.bring_to_front()
        action_container = detail_page.locator(self.selectors.action_container)
        if await action_container.count():
            await action_container.first.scroll_into_view_if_needed()
        if await self._sleep_or_stop(1.2, should_stop):
            return {
                "success": False,
                "already_delivered": False,
                "reason": "收到停止指令，中断当前岗位投递",
            }

        button = detail_page.locator(self.selectors.deliver_button).first
        before_snapshot = await self.inspect_deliver_button(detail_page)
        if not await self._wait_for_visible(button, should_stop, timeout_ms=8000):
            return {
                "success": False,
                "already_delivered": False,
                "reason": "收到停止指令，中断当前岗位投递",
            }
        await button.scroll_into_view_if_needed()
        await button.hover()

        try:
            await button.click(timeout=5000, no_wait_after=True)
            result = await self.evaluate_click_attempt(detail_page, context, before_snapshot, "普通点击", should_stop)
            if result is not None:
                return result
        except Exception:  # noqa: BLE001
            logger.warning("Normal deliver click failed.", exc_info=True)

        final_snapshot = await self.inspect_deliver_button(detail_page)
        return {
            "success": False,
            "already_delivered": False,
            "reason": (
                f"单次点击未触发状态变化 | 点击前：{before_snapshot} | 点击后：{final_snapshot}"
            ),
        }

    async def evaluate_click_attempt(
        self,
        detail_page,
        context,
        before_snapshot: str,
        click_mode: str,
        should_stop: Callable[[], bool],
    ) -> dict | None:
        detect_result = await self.wait_for_delivery_signal(context, detail_page, should_stop, timeout_ms=8000)
        after_snapshot = await self.inspect_deliver_button(detail_page)
        if detect_result.get("daily_limit_reached", False):
            return {
                "success": False,
                "already_delivered": False,
                "daily_limit_reached": True,
                "reason": (
                    f"点击方式：{click_mode} | 点击前：{before_snapshot} | 点击后：{after_snapshot} | {detect_result['reason']}"
                ),
            }
        if detect_result["success"] or after_snapshot != before_snapshot:
            return {
                "success": True,
                "already_delivered": False,
                "reason": (
                    f"点击方式：{click_mode} | 点击前：{before_snapshot} | 点击后：{after_snapshot} | {detect_result['reason']}"
                ),
            }
        return None

    async def wait_for_delivery_signal(
        self,
        context,
        detail_page,
        should_stop: Callable[[], bool],
        timeout_ms: int = 8000,
    ) -> dict:
        deadline = time.time() + timeout_ms / 1000
        last_reason = "未检测到投递成功标识"
        while time.time() < deadline:
            if should_stop():
                return {"success": False, "reason": "收到停止指令，中断投递结果等待"}
            detect_result = await self.detect_delivery_success(context, detail_page)
            if detect_result["success"] or detect_result.get("daily_limit_reached", False):
                return detect_result
            last_reason = detect_result["reason"]
            await self._sleep_or_stop(0.35, should_stop)
        return {"success": False, "reason": last_reason}

    async def inspect_deliver_button(self, detail_page) -> str:
        deliver_buttons = detail_page.locator(self.selectors.deliver_button)
        deliver_count = await deliver_buttons.count()
        delivered_buttons = detail_page.locator(self.selectors.delivered_button)
        delivered_count = await delivered_buttons.count()
        action_container = detail_page.locator(self.selectors.action_container)
        action_count = await action_container.count()

        if deliver_count:
            button = deliver_buttons.first
            try:
                text = (await button.inner_text()).strip()
            except Exception:  # noqa: BLE001
                text = "<unreadable>"
            try:
                disabled = await button.is_disabled()
            except Exception:  # noqa: BLE001
                disabled = False
            return (
                f"action_count={action_count}, deliver_count={deliver_count}, delivered_count={delivered_count}, "
                f"text={text}, disabled={disabled}"
            )

        return (
            f"action_count={action_count}, deliver_count={deliver_count}, delivered_count={delivered_count}, "
            "text=<missing>, disabled=<unknown>"
        )

    @staticmethod
    def merge_reasons(click_reason: str, detect_reason: str) -> str:
        return f"{click_reason} | {detect_reason}"

    async def wait_for_login(
        self,
        page,
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None = None,
        confirm_login_callback: Callable[[], bool] | None = None,
    ) -> None:
        while not should_stop():
            if confirm_login_callback and confirm_login_callback():
                if await self.is_login_required(page):
                    if status_callback:
                        status_callback("仍检测到未登录，请先完成智联登录后再次确认。")
                else:
                    if status_callback:
                        status_callback("已确认登录完成，开始执行搜索与投递。")
                    return
            if await self._sleep_or_stop(2.0, should_stop):
                return

    async def close_delivery_pages(
        self,
        context,
        list_page,
        detail_page,
        baseline_page_ids: set[int],
        target_entry_url: str,
        should_stop: Callable[[], bool],
        settle_timeout_ms: int = 2500,
    ):
        list_page = await self.ensure_list_page(context, list_page, target_entry_url)
        deadline = time.time() + settle_timeout_ms / 1000
        protected_page_ids = {id(list_page)} if list_page is not None else set()

        while time.time() < deadline:
            if should_stop():
                break

            with contextlib.suppress(Exception):
                if list_page is not None and not list_page.is_closed():
                    await list_page.bring_to_front()

            extra_pages: list = []
            for page in list(context.pages):
                if page.is_closed():
                    continue
                page_id = id(page)
                if page_id in protected_page_ids:
                    continue
                if page_id not in baseline_page_ids or page is detail_page:
                    extra_pages.append(page)

            if extra_pages:
                logger.info(
                    "Zhilian closing tracked extra pages: %s",
                    [self._safe_page_url(page) for page in extra_pages],
                )
                for page in extra_pages:
                    if page is not list_page:
                        await self._force_close_page(page, should_stop)
                if await self._sleep_or_stop(0.2, should_stop):
                    break
                continue

            break

        with contextlib.suppress(Exception):
            list_page = await self.ensure_list_page(context, list_page, target_entry_url)
            if not list_page.is_closed():
                await list_page.bring_to_front()
                await self._sleep_or_stop(0.5, should_stop)
        return list_page

    async def _force_close_page(self, page, should_stop: Callable[[], bool] | None = None) -> None:
        if page.is_closed():
            return

        page_url = self._safe_page_url(page)
        logger.info("Zhilian attempting to close extra page: %s", page_url)

        with contextlib.suppress(Exception):
            await asyncio.wait_for(page.close(), timeout=1.5)

        for _ in range(5):
            if page.is_closed():
                return
            if await self._sleep_or_stop(0.2, should_stop):
                return

        with contextlib.suppress(Exception):
            await asyncio.wait_for(page.evaluate("() => window.close()"), timeout=1.5)

        for _ in range(5):
            if page.is_closed():
                return
            if await self._sleep_or_stop(0.2, should_stop):
                return

        if not page.is_closed():
            logger.warning("Zhilian extra page still open after force close attempts: %s", self._safe_page_url(page))

    @staticmethod
    def _snapshot_open_pages(context) -> set[int]:
        return {id(page) for page in list(context.pages) if not page.is_closed()}

    async def _wait_for_visible(
        self,
        locator,
        should_stop: Callable[[], bool],
        timeout_ms: int = 8000,
        step_ms: int = 200,
    ) -> bool:
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            if should_stop():
                return False
            with contextlib.suppress(Exception):
                if await locator.count() and await locator.is_visible():
                    return True
            await self._sleep_or_stop(step_ms / 1000, should_stop)
        raise TimeoutError(f"元素在 {timeout_ms}ms 内未变为可见")

    async def _sleep_or_stop(
        self,
        seconds: float,
        should_stop: Callable[[], bool] | None = None,
        step: float = 0.2,
    ) -> bool:
        if seconds <= 0:
            return bool(should_stop and should_stop())

        deadline = time.time() + seconds
        while time.time() < deadline:
            if should_stop and should_stop():
                return True
            remaining = deadline - time.time()
            await asyncio.sleep(min(step, max(remaining, 0.01)))
        return bool(should_stop and should_stop())

    @staticmethod
    def _safe_page_url(page) -> str:
        with contextlib.suppress(Exception):
            return page.url
        return "<unknown>"

    async def extract_job_payload(self, detail_page, detail_url: str) -> dict:
        salary_text = await detail_page.locator(self.selectors.salary).inner_text()
        title = await detail_page.locator(self.selectors.title).inner_text()
        description = await detail_page.locator(self.selectors.description).inner_text()
        company_name = await detail_page.locator(self.selectors.company).inner_text()
        tags = await detail_page.locator(self.selectors.tag_items).all_inner_texts()
        summary_info = await self.extract_summary_info(detail_page)
        salary_min, salary_max = self.normalize_salary(salary_text)
        return {
            "platform_job_id": self.extract_job_code(detail_url),
            "title": title.strip(),
            "company_name": company_name.strip(),
            "salary_text": salary_text.strip(),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "description": description.strip(),
            "detail_url": detail_url,
            "tags": [tag.strip() for tag in tags if tag.strip()],
            "city": summary_info["city"],
            "experience_text": summary_info["experience_text"],
            "education_text": summary_info["education_text"],
        }

    async def extract_summary_info(self, detail_page) -> dict[str, str]:
        items = detail_page.locator(self.selectors.summary_info_items)
        count = await items.count()
        texts: list[str] = []

        for index in range(count):
            item = items.nth(index)
            text = await self._extract_summary_item_text(item)
            normalized = self._normalize_text(text)
            if normalized:
                texts.append(normalized)

        city = self._extract_city_from_summary(texts)
        experience_text = next((text for text in texts if self._is_experience_text(text)), "不限")
        education_text = next((text for text in texts if self._is_education_text(text)), "未标注")

        return {
            "city": city or "未标注",
            "experience_text": experience_text,
            "education_text": education_text,
        }

    async def _extract_summary_item_text(self, locator) -> str:
        with contextlib.suppress(Exception):
            text = await locator.evaluate(
                """(el) => {
                    const parts = Array.from(el.querySelectorAll('a, span'))
                      .map((node) => (node.textContent || '').replace(/\\s+/g, ' ').trim())
                      .filter(Boolean);
                    if (parts.length > 0) {
                      return parts.join('·');
                    }
                    return (el.textContent || '').replace(/\\s+/g, ' ').trim();
                }"""
            )
            return self._normalize_text(text)

        with contextlib.suppress(Exception):
            return self._normalize_text(await locator.inner_text())

        return ""

    def _extract_city_from_summary(self, texts: list[str]) -> str:
        for text in texts:
            if self._is_experience_text(text) or self._is_education_text(text):
                continue
            if any(token in text for token in ("全职", "兼职", "实习", "招", "人")):
                continue
            spaced_parts = [part for part in re.split(r"\s+", text) if part]
            if len(spaced_parts) >= 2:
                return "·".join(spaced_parts[:2])
            cleaned = re.sub(r"\s+", "", text).strip("·")
            if "·" in cleaned:
                parts = [part for part in cleaned.split("·") if part]
                if parts:
                    return "·".join(parts[:2])
            return cleaned
        return ""

    @staticmethod
    def _is_experience_text(value: str) -> bool:
        normalized = value.strip()
        return bool(re.search(r"(经验不限|不限|无经验|应届|在校|\d+\s*-\s*\d+\s*年|\d+\s*年)", normalized))

    @staticmethod
    def _is_education_text(value: str) -> bool:
        normalized = value.strip()
        return bool(re.search(r"(学历不限|不限|大专|专科|本科|硕士|博士|中专|中技|高中|初中)", normalized))

    @staticmethod
    def _normalize_text(value: str | None) -> str:
        if not value:
            return ""
        value = re.sub(r"\s+", " ", value)
        value = re.sub(r"[·]{2,}", "·", value)
        return value.strip(" ·")

    def run_sync_mock(self, keyword: str, per_job_callback: Callable[[dict], None], should_stop: Callable[[], bool]) -> None:
        samples = [
            {
                "platform_job_id": "CC302351080J40924781315",
                "title": "软件开发（无经验考研考公断档，不限语言）",
                "company_name": "广东上药桑尼克医疗科技有限公司",
                "salary_text": "1.5-3万",
                "salary_min": 15,
                "salary_max": 30,
                "description": "在指导下参与软件模块设计和维护。",
                "detail_url": "https://www.zhaopin.com/jobdetail/CC302351080J40924781315.htm",
                "tags": ["JavaScript", "CSS", "Spring"],
                "city": "广州·天河区",
                "experience_text": "不限",
                "education_text": "本科",
                "delivery_success": True,
                "status_reason": f"关键词 {keyword} 命中，已完成模拟投递。",
            },
            {
                "platform_job_id": "CC102937182J40281766609",
                "title": "全栈工程师",
                "company_name": "柏川科技",
                "salary_text": "8000-11000元",
                "salary_min": 8,
                "salary_max": 11,
                "description": "负责业务系统前后端协同开发。",
                "detail_url": "https://www.zhaopin.com/jobdetail/CC102937182J40281766609.htm",
                "tags": ["Vue3", "Python", "PostgreSQL"],
                "city": "广州·黄埔区",
                "experience_text": "1-3年",
                "education_text": "本科",
                "delivery_success": True,
                "status_reason": f"关键词 {keyword} 命中，职位满足薪资与城市条件。",
            },
        ]
        for sample in samples:
            if should_stop():
                break
            per_job_callback(sample)
            asyncio.run(asyncio.sleep(1.2))
