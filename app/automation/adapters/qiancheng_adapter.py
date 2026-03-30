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
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.automation.browser_factory import (
    LAODENG_INIT_SCRIPT,
    STEALTH_INIT_SCRIPT,
    build_browser_launch_options,
)
from app.automation.async_runner import run_async_blocking
from app.core.config import settings
from app.services.live_preview_service import live_preview_service

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class QianchengSelectors:
    login_links: str = 'a[href*="login.51job.com/login.php"]'
    login_register_button: str = "span.login.loginBtnClick"
    search_input: str = "#keywordInput"
    search_button: str = ".search-wrap .search-btn"
    search_keyword_label: str = ".search-wrap .search-input label"
    job_list: str = "div.joblist"
    job_card: str = "div.joblist-item"
    job_click_area: str = ".joblist-item-job"
    job_title: str = ".jname"
    job_company: str = ".cname"
    active_page: str = ".pageation .el-pager li.number.active"
    next_page: str = ".pageation .btn-next"
    title: str = ".tHeader.tHjob h1"
    job_id_input: str = "#hidJobID"
    salary: str = ".tHeader.tHjob strong"
    summary_line: str = ".tHeader.tHjob .msg.ltype"
    apply_button: str = "#app_ck"
    description: str = ".bmsg.job_msg.inbox"
    company_name: str = ".tBorderTop_box .com_msg .com_name p, .tBorderTop_box .com_msg .com_name"
    company_tags: str = ".tBorderTop_box .com_tag .at"
    success_panel: str = ".j_sucR"
    success_text: str = ".j_sucR .fw"
    campus_redirect_modal: str = ".el-dialog.wsjob"
    campus_redirect_header: str = ".el-dialog.wsjob .el-dialog__header"
    campus_redirect_body: str = ".el-dialog.wsjob .el-dialog__body"
    list_salary: str = ".sal"
    list_area: str = ".area"
    list_tags: str = ".tag"


class QianchengCampusRedirectRequired(RuntimeError):
    pass


class QianchengAdapter:
    def __init__(self) -> None:
        self.selectors = QianchengSelectors()
        self._runtime_lock = threading.Lock()
        self._active_loop = None
        self._active_context = None
        self._active_browser = None
        self._active_chrome_pid: int | None = None

    @staticmethod
    def extract_job_code(detail_url: str) -> str:
        if not detail_url:
            return ""
        for pattern in (r"/(\d+)\.html", r"[?&]jobid=(\d+)"):
            match = re.search(pattern, detail_url)
            if match:
                return match.group(1)
        parsed = urlparse(detail_url)
        return parsed.path.rstrip("/").split("/")[-1]

    @staticmethod
    def normalize_salary(raw_salary: str) -> tuple[float | None, float | None]:
        salary = (raw_salary or "").strip()
        if not salary or any(token in salary for token in ("面议", "待定")):
            return None, None

        salary = salary.replace(" ", "")
        salary = salary.split("·", maxsplit=1)[0]
        salary = salary.replace("以上", "").replace("以下", "")

        is_daily_salary = "天" in salary and "元" in salary and not any(token in salary for token in ("万", "千", "K", "k"))
        if is_daily_salary:
            if "-" not in salary:
                value = QianchengAdapter._parse_daily_salary_part(salary)
                return value, value
            left, right = salary.split("-", maxsplit=1)
            return (
                QianchengAdapter._parse_daily_salary_part(left),
                QianchengAdapter._parse_daily_salary_part(right),
            )

        normalized = (
            salary.replace("元/月", "")
            .replace("元", "")
            .replace("万", "w")
            .replace("千", "k")
            .replace("K", "k")
            .strip()
        )

        if "-" not in normalized:
            value = QianchengAdapter._parse_salary_part(normalized)
            return value, value

        left, right = normalized.split("-", maxsplit=1)
        left, right = QianchengAdapter._inherit_range_units(left, right)
        return QianchengAdapter._parse_salary_part(left), QianchengAdapter._parse_salary_part(right)

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

    @staticmethod
    def _parse_daily_salary_part(value: str) -> float | None:
        digits = re.findall(r"\d+(?:\.\d+)?", value)
        if not digits:
            return None
        return float(digits[0])

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

        del max_deliveries_per_run

        launch_headless = settings.browser_headless if headless is None else headless
        launch_mode = (
            "fake_headless" if launch_headless and settings.browser_fake_headless else "headless" if launch_headless else "visible"
        )
        logger.info(
            "Qiancheng launch mode resolved: mode=%s requested_headless=%s settings_headless=%s fake_headless=%s",
            launch_mode,
            headless,
            settings.browser_headless,
            settings.browser_fake_headless,
        )
        use_persistent_context = bool(launch_headless and not settings.browser_fake_headless)
        target_entry_url = (entry_url or settings.qiancheng_entry_url).strip() or settings.qiancheng_entry_url
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
                    raise RuntimeError("前程无忧外部 Chrome 已启动，但未能连接到调试端口。")
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

            try:
                await list_page.goto(target_entry_url, wait_until="domcontentloaded")
                if await self.is_login_required(list_page):
                    if status_callback:
                        status_callback("前程无忧当前未登录，请先在浏览器中完成登录，然后回到前端点击确认已登录。")
                    await self.wait_for_login(list_page, should_stop, status_callback, confirm_login_callback)
                    if should_stop():
                        return

                for search_term in effective_search_terms:
                    if should_stop():
                        break

                    search_label = (search_term or "").strip() or "默认搜索页"
                    preview_ref["page"] = list_page
                    list_page = await self.prepare_search_results_page(
                        list_page,
                        target_entry_url,
                        search_term,
                        should_stop,
                        status_callback,
                        use_manual_entry_url=use_manual_entry_url,
                    )
                    if await self.is_login_required(list_page):
                        if status_callback:
                            status_callback("前程无忧当前未登录，请先在浏览器中完成登录，然后回到前端点击确认已登录。")
                        await self.wait_for_login(list_page, should_stop, status_callback, confirm_login_callback)
                        if should_stop():
                            return
                        list_page = await self.prepare_search_results_page(
                            list_page,
                            target_entry_url,
                            search_term,
                            should_stop,
                            status_callback,
                            use_manual_entry_url=use_manual_entry_url,
                        )
                    if should_stop():
                        break

                    while not should_stop():
                        list_page = await self.ensure_list_page(context, list_page, target_entry_url)
                        cards = list_page.locator(self.selectors.job_card)
                        count = await cards.count()
                        if count == 0:
                            logger.warning("No Qiancheng job cards found on current page.")
                            if status_callback:
                                status_callback(f"{search_label} 当前页未找到岗位卡片，切换下一组条件。")
                            break

                        for index in range(count):
                            if should_stop():
                                break

                            if status_callback:
                                status_callback(f"{search_label}：正在打开第 {index + 1} 个岗位详情")

                            list_page = await self.ensure_list_page(context, list_page, target_entry_url)
                            cards = list_page.locator(self.selectors.job_card)
                            if index >= await cards.count():
                                break

                            list_card_payload = await self.extract_list_card_payload(cards.nth(index), self._safe_page_url(list_page))
                            pages_before_detail = self._snapshot_open_pages(context)
                            try:
                                detail_page = await self.open_detail_page(context, list_page, cards.nth(index))
                            except QianchengCampusRedirectRequired as exc:
                                if status_callback:
                                    status_callback(f"{search_label}：检测到校招网申跳转，等待 10 秒后跳过当前职位")
                                list_card_payload["search_term"] = search_label
                                list_card_payload["already_delivered"] = False
                                list_card_payload["delivery_success"] = False
                                list_card_payload["status_reason"] = str(exc)
                                per_job_callback(list_card_payload)
                                preview_ref["page"] = list_page
                                stopped_while_waiting, list_page = await self.wait_and_close_campus_redirect_tabs(
                                    context,
                                    list_page,
                                    pages_before_detail,
                                    target_entry_url,
                                    should_stop,
                                    wait_seconds=10.0,
                                )
                                if stopped_while_waiting:
                                    break
                                list_page = await self.ensure_list_page(context, list_page, target_entry_url)
                                if status_callback:
                                    status_callback(f"{search_label}：已关闭校招跳转标签，继续当前列表页")
                                preview_ref["page"] = list_page
                                continue
                            preview_ref["page"] = detail_page
                            stop_after_cleanup = False
                            job_payload = None
                            detail_url = self._safe_page_url(detail_page)

                            try:
                                await detail_page.wait_for_load_state("domcontentloaded")
                                if await self._sleep_or_stop(1.5, should_stop):
                                    stop_after_cleanup = True
                                    continue

                                detail_url = detail_page.url
                                job_payload = await self.extract_job_payload(detail_page, detail_url)
                                job_payload["search_term"] = search_label
                                job_payload["already_delivered"] = False
                                delivery_result = await self.try_deliver(detail_page, should_stop)
                                job_payload["already_delivered"] = bool(delivery_result.get("already_delivered", False))
                                job_payload["delivery_success"] = bool(delivery_result.get("success", False)) and not job_payload["already_delivered"]
                                job_payload["status_reason"] = str(delivery_result.get("reason", "未完成投递"))
                            except (PlaywrightTimeoutError, TimeoutError):
                                logger.info("Qiancheng apply button not clickable for %s", detail_url)
                                if job_payload is not None:
                                    job_payload["already_delivered"] = False
                                    job_payload["delivery_success"] = False
                                    job_payload["status_reason"] = "未找到可点击的申请职位按钮"
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

                            if stop_after_cleanup:
                                break

                            if delivery_interval_seconds > 0 and await self._sleep_or_stop(float(delivery_interval_seconds), should_stop):
                                break

                        next_page = list_page.locator(self.selectors.next_page).first
                        if await next_page.count() == 0:
                            break
                        disabled = (await next_page.get_attribute("disabled")) or ""
                        class_name = (await next_page.get_attribute("class")) or ""
                        if disabled or "disabled" in class_name.lower():
                            break

                        if status_callback:
                            current_page_number = await self._read_active_page_index(list_page) or 1
                            status_callback(f"{search_label} 当前页处理完成，准备进入第 {current_page_number + 1} 页")

                        await self.go_to_next_page(list_page, should_stop, status_callback)
                        if await self._sleep_or_stop(2.0, should_stop):
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
                    await live_preview_service.capture_page("qiancheng", task_run_id, page)
            await asyncio.sleep(interval)

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
        logger.info("Started external Chrome for Qiancheng task: pid=%s port=%s", pid_text, debug_port)
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

    async def is_login_required(self, page) -> bool:
        if self._is_login_redirect(self._safe_page_url(page)):
            return True
        login_links = page.locator(self.selectors.login_links)
        with contextlib.suppress(Exception):
            if await login_links.count():
                first = login_links.first
                if await first.is_visible():
                    return True
        login_register_button = page.locator(self.selectors.login_register_button)
        with contextlib.suppress(Exception):
            if await login_register_button.count():
                first = login_register_button.first
                if await first.is_visible():
                    text = self._normalize_text(await self._safe_inner_text(first))
                    if not text or "登录" in text or "注册" in text:
                        return True
        return False

    @staticmethod
    def _is_login_redirect(url: str) -> bool:
        normalized = (url or "").lower()
        return "login.51job.com/login.php" in normalized

    def _build_search_url(self, base_url: str, search_term: str) -> str:
        parsed = urlparse(base_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        keyword = (search_term or "").strip()
        if keyword:
            query["keyword"] = keyword
        elif "keyword" not in query:
            query["keyword"] = ""
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    async def prepare_search_results_page(
        self,
        list_page,
        target_entry_url: str,
        search_term: str,
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None = None,
        *,
        use_manual_entry_url: bool = False,
    ):
        search_keyword = (search_term or "").strip()
        search_label = search_keyword or "默认搜索页"
        open_message = "正在打开手动配置的前程无忧列表页" if use_manual_entry_url else "正在打开前程无忧搜索页"
        if status_callback:
            suffix = f"：{search_label}" if search_keyword or not use_manual_entry_url else ""
            status_callback(f"{open_message}{suffix}")

        await list_page.goto(target_entry_url, wait_until="domcontentloaded")
        if await self._sleep_or_stop(1.2, should_stop):
            return list_page

        if not search_keyword:
            if status_callback:
                status_callback("未提供搜索词，使用前程无忧默认搜索页继续执行")
            return list_page

        if use_manual_entry_url:
            if status_callback:
                status_callback(f"当前使用手动页面，但仍会在页面内搜索：{search_keyword}")
        else:
            if status_callback:
                status_callback(f"准备在前程无忧页面内搜索：{search_keyword}")

        try:
            await self.execute_page_search(list_page, search_keyword, should_stop)
        except Exception:  # noqa: BLE001
            logger.warning("Qiancheng in-page search failed, falling back to query URL.", exc_info=True)
            fallback_url = self._build_search_url(target_entry_url, search_keyword)
            await list_page.goto(fallback_url, wait_until="domcontentloaded")
            await self._sleep_or_stop(1.5, should_stop)
            return list_page

        if status_callback:
            status_callback(f"前程无忧搜索已提交：{search_keyword}")
        return list_page

    async def execute_page_search(
        self,
        list_page,
        search_term: str,
        should_stop: Callable[[], bool],
    ) -> None:
        keyword = (search_term or "").strip()
        if not keyword:
            return

        search_input = list_page.locator(self.selectors.search_input).first
        search_button = list_page.locator(self.selectors.search_button).first
        await self._wait_for_visible(search_input, should_stop, timeout_ms=12000)
        with contextlib.suppress(Exception):
            await search_input.scroll_into_view_if_needed()

        previous_url = self._safe_page_url(list_page)
        previous_signature = await self._read_page_signature(list_page)

        await search_input.click(timeout=3000)
        with contextlib.suppress(Exception):
            await search_input.press("Control+A")
        await search_input.fill("")
        await search_input.fill(keyword)

        clicked = False
        try:
            await search_button.click(timeout=5000)
            clicked = True
        except Exception:  # noqa: BLE001
            logger.warning("Qiancheng search button click failed, retrying with Enter.", exc_info=True)

        if not clicked:
            await search_input.press("Enter")

        updated = await self._wait_for_search_results(
            list_page,
            keyword,
            should_stop,
            previous_url=previous_url,
            previous_signature=previous_signature,
        )
        if not updated:
            raise RuntimeError("前程无忧搜索后列表未刷新，无法确认搜索已生效")

    async def _wait_for_search_results(
        self,
        list_page,
        search_term: str,
        should_stop: Callable[[], bool],
        *,
        previous_url: str,
        previous_signature: str,
        timeout_ms: int = 12000,
    ) -> bool:
        deadline = time.time() + timeout_ms / 1000
        expected_keyword = (search_term or "").strip()

        while time.time() < deadline:
            if should_stop():
                return False

            current_url = self._safe_page_url(list_page)
            current_signature = await self._read_page_signature(list_page)
            current_keyword = await self._read_current_search_keyword(list_page)
            job_cards = list_page.locator(self.selectors.job_card)
            job_count = 0
            with contextlib.suppress(Exception):
                job_count = await job_cards.count()
            url_keyword = self._read_keyword_from_url(current_url)

            keyword_matches = not expected_keyword or current_keyword == expected_keyword or url_keyword == expected_keyword
            content_updated = bool(
                current_url != previous_url
                or current_signature != previous_signature
                or (job_count > 0 and not previous_signature)
            )
            if keyword_matches and content_updated:
                return True

            await self._sleep_or_stop(0.25, should_stop)
        return False

    async def _read_current_search_keyword(self, list_page) -> str:
        search_input = list_page.locator(self.selectors.search_input).first
        with contextlib.suppress(Exception):
            value = self._normalize_text(await search_input.input_value())
            if value:
                return value
        with contextlib.suppress(Exception):
            label = list_page.locator(self.selectors.search_keyword_label).first
            value = self._normalize_text((await label.get_attribute("data-after")) or "")
            if value:
                return value
        return ""

    @staticmethod
    def _read_keyword_from_url(url: str) -> str:
        parsed = urlparse(url or "")
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        return (query.get("keyword") or "").strip()

    async def open_detail_page(self, context, list_page, card_locator):
        with contextlib.suppress(Exception):
            await list_page.bring_to_front()

        click_target = card_locator.locator(self.selectors.job_click_area).first
        target = click_target if await click_target.count() else card_locator
        with contextlib.suppress(Exception):
            await target.scroll_into_view_if_needed()

        current_url = self._safe_page_url(list_page)
        try:
            async with context.expect_page(timeout=6000) as page_info:
                await target.click(timeout=5000, no_wait_after=True)
            page = await page_info.value
            await page.wait_for_load_state("domcontentloaded")
            redirect_reason = await self.wait_for_campus_redirect_signal(list_page, page)
            if redirect_reason:
                if page is not list_page:
                    await self._force_close_page(page)
                raise QianchengCampusRedirectRequired(redirect_reason)
            return page
        except QianchengCampusRedirectRequired:
            raise
        except Exception:  # noqa: BLE001
            logger.info("Qiancheng detail page did not open in a new tab, retrying same-tab detection.")
            redirect_reason = await self.detect_campus_redirect_modal(list_page)
            if redirect_reason:
                raise QianchengCampusRedirectRequired(redirect_reason)

        await target.click(timeout=5000, no_wait_after=True)
        await list_page.wait_for_load_state("domcontentloaded")
        if self._safe_page_url(list_page) != current_url:
            return list_page
        redirect_reason = await self.detect_campus_redirect_modal(list_page)
        if redirect_reason:
            raise QianchengCampusRedirectRequired(redirect_reason)
        raise RuntimeError("前程无忧职位详情页未能成功打开")

    async def detect_campus_redirect_modal(self, page) -> str:
        modal = page.locator(self.selectors.campus_redirect_modal).first
        with contextlib.suppress(Exception):
            if not await modal.count() or not await modal.is_visible():
                return ""
            header = self._normalize_text(await self._safe_inner_text(page.locator(self.selectors.campus_redirect_header).first))
            body = self._normalize_text(await self._safe_inner_text(page.locator(self.selectors.campus_redirect_body).first))
            merged = " ".join(part for part in (header, body) if part).strip()
            if "应届生求职平台" in merged or "校招网申职位" in merged:
                return merged or "当前职位需要跳转应届生平台，已跳过本次投递"
        return ""

    async def wait_for_campus_redirect_signal(
        self,
        *pages,
        timeout_ms: int = 1500,
        step_ms: int = 150,
    ) -> str:
        candidates = [page for page in pages if page is not None and not page.is_closed()]
        if not candidates:
            return ""

        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            for page in candidates:
                reason = await self.detect_campus_redirect_modal(page)
                if reason:
                    return reason
            await asyncio.sleep(step_ms / 1000)
        return ""

    async def go_to_next_page(
        self,
        list_page,
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        next_page = list_page.locator(self.selectors.next_page).first
        if await next_page.count() == 0:
            raise RuntimeError("前程无忧当前页不存在“下一页”按钮，无法继续翻页")

        disabled = (await next_page.get_attribute("disabled")) or ""
        class_name = (await next_page.get_attribute("class")) or ""
        if disabled or "disabled" in class_name.lower():
            raise RuntimeError("前程无忧当前已到最后一页，无法继续翻页")

        current_page_index = await self._read_active_page_index(list_page)
        previous_signature = await self._read_page_signature(list_page)

        try:
            await self._prepare_next_page_click(list_page, next_page, should_stop)
            await next_page.click(timeout=5000)
            if await self._wait_for_page_turn(
                list_page,
                should_stop,
                previous_page_index=current_page_index,
                previous_signature=previous_signature,
            ):
                return
        except Exception:  # noqa: BLE001
            logger.warning("Qiancheng next page normal click failed", exc_info=True)

        await self._prepare_next_page_click(list_page, next_page, should_stop)
        await next_page.evaluate("(el) => el.click()")
        if await self._wait_for_page_turn(
            list_page,
            should_stop,
            previous_page_index=current_page_index,
            previous_signature=previous_signature,
        ):
            if status_callback:
                status_callback("前程无忧翻页已切换为脚本点击模式")
            return

        raise RuntimeError("前程无忧翻页失败：列表内容没有发生变化")

    async def _wait_for_page_turn(
        self,
        list_page,
        should_stop: Callable[[], bool],
        *,
        previous_page_index: int | None,
        previous_signature: str,
        timeout_ms: int = 10000,
    ) -> bool:
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            if should_stop():
                return False

            current_page_index = await self._read_active_page_index(list_page)
            current_signature = await self._read_page_signature(list_page)
            signature_changed = bool(current_signature and current_signature != previous_signature)

            if (
                previous_page_index is not None
                and current_page_index is not None
                and current_page_index != previous_page_index
                and signature_changed
            ):
                return True

            await self._sleep_or_stop(0.25, should_stop)
        return False

    async def _prepare_next_page_click(self, list_page, next_page, should_stop: Callable[[], bool]) -> None:
        await list_page.evaluate(
            """() => {
                const doc = document.documentElement || document.body;
                const height = Math.max(
                  doc?.scrollHeight || 0,
                  document.body?.scrollHeight || 0,
                  document.documentElement?.scrollHeight || 0
                );
                window.scrollTo({ top: height, behavior: 'instant' });
            }"""
        )
        if await self._sleep_or_stop(0.2, should_stop):
            return
        with contextlib.suppress(Exception):
            await next_page.scroll_into_view_if_needed()
        await self._sleep_or_stop(0.2, should_stop)

    async def _read_page_signature(self, list_page, sample_size: int = 3) -> str:
        cards = list_page.locator(self.selectors.job_card)
        with contextlib.suppress(Exception):
            count = min(await cards.count(), sample_size)
            parts: list[str] = []
            for index in range(count):
                card = cards.nth(index)
                job_id = await self._extract_list_card_job_id(card)
                title = await self._extract_list_card_title(card)
                parts.append(f"{job_id}|{title}")
            return " || ".join(parts)
        return ""

    async def _extract_list_card_title(self, card_locator) -> str:
        locator = card_locator.locator(self.selectors.job_title).first
        return self._normalize_text(await self._safe_inner_text(locator))

    async def _extract_list_card_job_id(self, card_locator) -> str:
        with contextlib.suppress(Exception):
            return await card_locator.evaluate(
                """(el) => {
                    const node = el.querySelector('.joblist-item-job');
                    const raw = node?.getAttribute('sensorsdata') || '';
                    if (!raw) return '';
                    try {
                        const parsed = JSON.parse(raw);
                        return String(parsed.jobId || '');
                    } catch (err) {
                        const match = raw.match(/jobId["']?\\s*:\\s*["']?(\\d+)/);
                        return match ? match[1] : '';
                    }
                }"""
            )
        return ""

    async def extract_list_card_payload(self, card_locator, list_url: str = "") -> dict:
        summary = {
            "platform_job_id": "",
            "title": "",
            "company_name": "",
            "salary_text": "",
            "city": "",
            "experience_text": "",
            "education_text": "",
            "tags": [],
        }
        with contextlib.suppress(Exception):
            raw_summary = await card_locator.evaluate(
                """(el) => {
                    const text = (selector) => {
                        const node = el.querySelector(selector);
                        return (node?.textContent || '').replace(/\\s+/g, ' ').trim();
                    };
                    const tags = Array.from(el.querySelectorAll('.tag'))
                      .map((node) => (node.textContent || '').replace(/\\s+/g, ' ').trim())
                      .filter(Boolean);
                    const jobNode = el.querySelector('.joblist-item-job');
                    const raw = jobNode?.getAttribute('sensorsdata') || '';
                    let sensors = {};
                    try {
                        sensors = raw ? JSON.parse(raw) : {};
                    } catch (err) {
                        sensors = {};
                    }
                    return {
                        platform_job_id: String(sensors.jobId || ''),
                        title: text('.jname') || String(sensors.jobTitle || ''),
                        company_name: text('.cname'),
                        salary_text: text('.sal') || String(sensors.jobSalary || ''),
                        city: text('.area') || String(sensors.jobArea || ''),
                        experience_text: String(sensors.jobYear || ''),
                        education_text: String(sensors.jobDegree || ''),
                        tags,
                    };
                }"""
            )
            if isinstance(raw_summary, dict):
                summary |= raw_summary

        platform_job_id = self._normalize_text(str(summary.get("platform_job_id") or ""))
        title = self._normalize_text(str(summary.get("title") or "")) or "未识别职位"
        company_name = self._normalize_text(str(summary.get("company_name") or "")) or "未识别公司"
        salary_text = self._normalize_text(str(summary.get("salary_text") or "")) or "未标注"
        city = self._normalize_text(str(summary.get("city") or "")) or "未标注"
        experience_text = self._normalize_text(str(summary.get("experience_text") or "")) or "不限"
        education_text = self._normalize_text(str(summary.get("education_text") or "")) or "未标注"
        tags = [
            self._normalize_text(str(item))
            for item in summary.get("tags", [])
            if self._normalize_text(str(item))
        ]
        salary_min, salary_max = self.normalize_salary(salary_text)

        return {
            "platform_job_id": platform_job_id or await self._extract_list_card_job_id(card_locator),
            "title": title,
            "company_name": company_name,
            "salary_text": salary_text,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "description": "",
            "detail_url": list_url,
            "tags": tags,
            "city": city,
            "experience_text": experience_text,
            "education_text": education_text,
        }

    async def _read_active_page_index(self, list_page) -> int | None:
        locator = list_page.locator(self.selectors.active_page).first
        with contextlib.suppress(Exception):
            if await locator.count():
                text = self._normalize_text(await locator.inner_text())
                if text.isdigit():
                    return int(text)
        return None

    async def ensure_list_page(self, context, current_page, target_entry_url: str):
        if current_page is not None and not current_page.is_closed() and await self._is_probable_list_page(current_page, target_entry_url):
            return current_page

        list_pages = await self._collect_list_pages(context, target_entry_url)
        if list_pages:
            logger.info(
                "Qiancheng recovered list page from existing tabs: %s",
                self._safe_page_url(list_pages[0]),
            )
            return list_pages[0]

        raise RuntimeError("前程无忧岗位列表页已被关闭，无法继续执行")

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
            settings.qiancheng_entry_url,
            "https://we.51job.com/pc/search",
        )
        if any(page_url.startswith(prefix) for prefix in fallback_prefixes if prefix):
            return True
        with contextlib.suppress(Exception):
            if await page.locator(self.selectors.job_card).count():
                return True
        return False

    async def detect_delivery_success(self, detail_page) -> dict:
        current_url = self._safe_page_url(detail_page)
        if "applysuccess.php" in current_url:
            return {"success": True, "reason": f"成功检测：跳转到投递成功页 {current_url}"}
        if self._is_login_redirect(current_url):
            return {"success": False, "login_required": True, "reason": "申请职位后跳转到登录页，请重新登录账号"}

        with contextlib.suppress(Exception):
            success_panel = detail_page.locator(self.selectors.success_panel).first
            if await success_panel.count():
                success_text = detail_page.locator(self.selectors.success_text).first
                header_text = self._normalize_text(await self._safe_inner_text(success_text))
                panel_text = self._normalize_multiline_text(await self._safe_inner_text(success_panel))
                merged = " | ".join(part for part in (header_text, panel_text) if part).strip()
                success_markers = ("投递成功", "申请成功", "成功投递", "前往应届生求职App")
                if any(marker in merged for marker in success_markers):
                    return {"success": True, "reason": f"成功检测：{merged or '检测到投递成功面板'}"}

        return {"success": False, "reason": f"未检测到投递成功标识 | {await self.inspect_deliver_button(detail_page)}"}

    async def try_deliver(self, detail_page, should_stop: Callable[[], bool]) -> dict:
        pre_detect = await self.detect_delivery_success(detail_page)
        if pre_detect.get("success"):
            return {
                "success": False,
                "already_delivered": True,
                "reason": f"点击前检测：当前详情页已经是投递成功状态 | {pre_detect['reason']}",
            }
        if pre_detect.get("login_required"):
            return {
                "success": False,
                "already_delivered": False,
                "reason": pre_detect["reason"],
            }

        await detail_page.bring_to_front()
        button = detail_page.locator(self.selectors.apply_button).first
        before_snapshot = await self.inspect_deliver_button(detail_page)

        try:
            visible = await self._wait_for_visible(button, should_stop, timeout_ms=8000)
        except TimeoutError:
            detect_result = await self.detect_delivery_success(detail_page)
            return {
                "success": bool(detect_result.get("success", False)),
                "already_delivered": False,
                "reason": detect_result.get("reason", f"未找到可点击的申请职位按钮 | {before_snapshot}"),
            }

        if not visible:
            return {
                "success": False,
                "already_delivered": False,
                "reason": "收到停止指令，中断当前岗位投递",
            }

        with contextlib.suppress(Exception):
            await button.scroll_into_view_if_needed()
        with contextlib.suppress(Exception):
            await button.hover()

        try:
            await button.click(timeout=5000, no_wait_after=True)
            result = await self.evaluate_click_attempt(detail_page, before_snapshot, "普通点击", should_stop)
            if result is not None:
                return result
        except Exception:  # noqa: BLE001
            logger.warning("Qiancheng normal apply click failed.", exc_info=True)

        try:
            await button.evaluate("(el) => el.click()")
            result = await self.evaluate_click_attempt(detail_page, before_snapshot, "脚本点击", should_stop)
            if result is not None:
                return result
        except Exception:  # noqa: BLE001
            logger.warning("Qiancheng script apply click failed.", exc_info=True)

        final_snapshot = await self.inspect_deliver_button(detail_page)
        return {
            "success": False,
            "already_delivered": False,
            "reason": f"单次点击未触发状态变化 | 点击前：{before_snapshot} | 点击后：{final_snapshot}",
        }

    async def evaluate_click_attempt(
        self,
        detail_page,
        before_snapshot: str,
        click_mode: str,
        should_stop: Callable[[], bool],
    ) -> dict | None:
        detect_result = await self.wait_for_delivery_signal(detail_page, should_stop, timeout_ms=10000)
        after_snapshot = await self.inspect_deliver_button(detail_page)
        if detect_result.get("success", False):
            return {
                "success": True,
                "already_delivered": False,
                "reason": (
                    f"点击方式：{click_mode} | 点击前：{before_snapshot} | 点击后：{after_snapshot} | {detect_result['reason']}"
                ),
            }
        if detect_result.get("login_required", False):
            return {
                "success": False,
                "already_delivered": False,
                "reason": (
                    f"点击方式：{click_mode} | 点击前：{before_snapshot} | 点击后：{after_snapshot} | {detect_result['reason']}"
                ),
            }
        return None

    async def wait_for_delivery_signal(
        self,
        detail_page,
        should_stop: Callable[[], bool],
        timeout_ms: int = 10000,
    ) -> dict:
        deadline = time.time() + timeout_ms / 1000
        last_reason = "未检测到投递成功标识"
        while time.time() < deadline:
            if should_stop():
                return {"success": False, "reason": "收到停止指令，中断投递结果等待"}
            with contextlib.suppress(Exception):
                await detail_page.wait_for_load_state("domcontentloaded", timeout=600)
            try:
                detect_result = await self.detect_delivery_success(detail_page)
            except Exception as exc:  # noqa: BLE001
                last_reason = f"详情页刷新中，继续等待投递结果 | {exc.__class__.__name__}"
                await self._sleep_or_stop(0.35, should_stop)
                continue
            if detect_result.get("success", False) or detect_result.get("login_required", False):
                return detect_result
            last_reason = detect_result.get("reason", last_reason)
            await self._sleep_or_stop(0.35, should_stop)
        return {"success": False, "reason": last_reason}

    async def inspect_deliver_button(self, detail_page) -> str:
        current_url = self._safe_page_url(detail_page)
        with contextlib.suppress(Exception):
            success_panel = detail_page.locator(self.selectors.success_panel).first
            if await success_panel.count():
                success_text = self._normalize_text(await self._safe_inner_text(success_panel))
                return f"url={current_url}, success_panel={success_text or '<present>'}"

        apply_buttons = detail_page.locator(self.selectors.apply_button)
        apply_count: int | None = None
        with contextlib.suppress(Exception):
            apply_count = await apply_buttons.count()
        if apply_count is None:
            return f"url={current_url}, count=<unavailable>, text=<unavailable>, disabled=<unknown>"

        if apply_count:
            button = apply_buttons.first
            try:
                text = self._normalize_text(await button.inner_text())
            except Exception:  # noqa: BLE001
                text = "<unreadable>"
            try:
                disabled = await button.is_disabled()
            except Exception:  # noqa: BLE001
                disabled = False
            return f"url={current_url}, count={apply_count}, text={text}, disabled={disabled}"

        return f"url={current_url}, count=0, text=<missing>, disabled=<unknown>"

    async def wait_for_login(
        self,
        page,
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None = None,
        confirm_login_callback: Callable[[], bool] | None = None,
    ) -> None:
        while not should_stop():
            if confirm_login_callback and confirm_login_callback():
                with contextlib.suppress(Exception):
                    await page.reload(wait_until="domcontentloaded")
                if await self.is_login_required(page):
                    if status_callback:
                        status_callback("仍检测到未登录，请先完成前程无忧登录后再次确认。")
                else:
                    if status_callback:
                        status_callback("已确认前程无忧登录完成，开始执行搜索与投递。")
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
        deadline = time.time() + settle_timeout_ms / 1000
        protected_page_ids = {id(list_page)} if list_page is not None else set()

        while time.time() < deadline:
            if should_stop():
                break

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
                    "Qiancheng closing tracked extra pages: %s",
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

    async def wait_and_close_campus_redirect_tabs(
        self,
        context,
        list_page,
        baseline_page_ids: set[int],
        target_entry_url: str,
        should_stop: Callable[[], bool],
        wait_seconds: float = 10.0,
        poll_interval_seconds: float = 0.5,
    ) -> tuple[bool, object]:
        deadline = time.time() + max(wait_seconds, 0.0)
        while time.time() < deadline:
            if should_stop():
                return True, list_page

            list_page = await self.close_delivery_pages(
                context,
                list_page,
                None,
                baseline_page_ids,
                target_entry_url,
                should_stop,
                settle_timeout_ms=600,
            )

            remaining = deadline - time.time()
            if remaining <= 0:
                break
            if await self._sleep_or_stop(min(poll_interval_seconds, remaining), should_stop):
                return True, list_page

        list_page = await self.close_delivery_pages(
            context,
            list_page,
            None,
            baseline_page_ids,
            target_entry_url,
            should_stop,
            settle_timeout_ms=1200,
        )
        return False, list_page

    async def _force_close_page(self, page, should_stop: Callable[[], bool] | None = None) -> None:
        if page.is_closed():
            return

        with contextlib.suppress(Exception):
            await asyncio.wait_for(page.close(), timeout=1.5)

        for _ in range(5):
            if page.is_closed():
                return
            if await self._sleep_or_stop(0.2, should_stop):
                return

        with contextlib.suppress(Exception):
            await asyncio.wait_for(page.evaluate("() => window.close()"), timeout=1.5)

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
        salary_text = self._normalize_text(await self._safe_inner_text(detail_page.locator(self.selectors.salary).first))
        title = self._normalize_text(await self._safe_inner_text(detail_page.locator(self.selectors.title).first))
        description = await self._extract_detail_description(detail_page)
        company_name = self._normalize_text(
            await self._safe_inner_text(detail_page.locator(self.selectors.company_name).first)
        )
        tags = await self._extract_tags(detail_page)
        summary_info = await self.extract_summary_info(detail_page)
        salary_min, salary_max = self.normalize_salary(salary_text)
        job_id = await self._extract_detail_job_id(detail_page)
        return {
            "platform_job_id": job_id or self.extract_job_code(detail_url),
            "title": title,
            "company_name": company_name,
            "salary_text": salary_text,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "description": description,
            "detail_url": detail_url,
            "tags": tags,
            "city": summary_info["city"],
            "experience_text": summary_info["experience_text"],
            "education_text": summary_info["education_text"],
        }

    async def extract_summary_info(self, detail_page) -> dict[str, str]:
        summary_line = self._normalize_text(
            await self._safe_inner_text(detail_page.locator(self.selectors.summary_line).first)
        )
        parts = [self._normalize_text(part) for part in re.split(r"\|", summary_line) if self._normalize_text(part)]
        city = parts[0] if parts else "未标注"
        experience_text = next((part for part in parts if self._is_experience_text(part)), "不限")
        education_text = next((part for part in parts if self._is_education_text(part)), "未标注")
        return {
            "city": city,
            "experience_text": experience_text,
            "education_text": education_text,
        }

    async def _extract_detail_description(self, detail_page) -> str:
        locator = detail_page.locator(self.selectors.description).first
        with contextlib.suppress(Exception):
            text = await locator.evaluate(
                """(el) => {
                    if (!(el instanceof HTMLElement)) {
                        return el?.textContent || '';
                    }
                    return el.innerText || el.textContent || '';
                }"""
            )
            return self._normalize_multiline_text(text)
        return self._normalize_multiline_text(await self._safe_inner_text(locator))

    async def _extract_tags(self, detail_page) -> list[str]:
        selectors = (
            ".tHeader.tHjob .jtag .t1 span",
            ".bmsg.job_msg.inbox .mt10 a",
            ".job_msg .mt10 a",
            self.selectors.company_tags,
        )
        seen: set[str] = set()
        tags: list[str] = []
        for selector in selectors:
            locator = detail_page.locator(selector)
            with contextlib.suppress(Exception):
                texts = await locator.all_inner_texts()
                for text in texts:
                    normalized = self._normalize_text(text)
                    if not normalized or normalized in seen:
                        continue
                    seen.add(normalized)
                    tags.append(normalized)
        return tags

    async def _extract_detail_job_id(self, detail_page) -> str:
        locator = detail_page.locator(self.selectors.job_id_input).first
        with contextlib.suppress(Exception):
            value = (await locator.get_attribute("value") or "").strip()
            if value:
                return value
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
        value = re.sub(r"[\u200b-\u200d\ufeff]", "", value)
        return value.strip(" |·")

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
    async def _safe_inner_text(locator) -> str:
        try:
            return (await locator.inner_text()).strip()
        except Exception:  # noqa: BLE001
            return ""

    def run_sync_mock(self, keyword: str, per_job_callback: Callable[[dict], None], should_stop: Callable[[], bool]) -> None:
        samples = [
            {
                "platform_job_id": "171084239",
                "title": "后端开发工程师",
                "company_name": "深圳市鼎森电子科技有限公司",
                "salary_text": "1-1.3万",
                "salary_min": 10,
                "salary_max": 13,
                "description": "依据公司业务需求完成 Java 系统设计与开发工作。",
                "detail_url": "https://jobs.51job.com/shenzhen/171084239.html",
                "tags": ["nodejs", "nest", "五险一金"],
                "city": "深圳·福田区",
                "experience_text": "1年及以上",
                "education_text": "本科",
                "delivery_success": True,
                "status_reason": f"关键词 {keyword or '默认搜索页'} 命中，已完成模拟投递。",
            },
            {
                "platform_job_id": "162160204",
                "title": "Java开发工程师",
                "company_name": "广州广日电梯工业有限公司",
                "salary_text": "7千-1万·13薪",
                "salary_min": 7,
                "salary_max": 10,
                "description": "负责业务系统模块开发与维护。",
                "detail_url": "https://jobs.51job.com/guangzhou/162160204.html",
                "tags": ["Java", "国企", "1000-5000人"],
                "city": "广州",
                "experience_text": "1年及以上",
                "education_text": "本科",
                "delivery_success": True,
                "status_reason": f"关键词 {keyword or '默认搜索页'} 命中，职位满足筛选条件。",
            },
        ]
        for sample in samples:
            if should_stop():
                break
            per_job_callback(sample)
            asyncio.run(asyncio.sleep(1.2))

    def import_resume_snapshot(
        self,
        profile_dir: str,
        *,
        headless: bool | None = None,
        target_url: str = "https://www.51job.com/resume/center?lang=c",
    ) -> dict:
        return run_async_blocking(self._import_resume_snapshot(profile_dir, headless=headless, target_url=target_url))

    def sync_master_resume(
        self,
        profile_dir: str,
        master_resume: dict,
        *,
        headless: bool | None = None,
        target_url: str = "https://www.51job.com/resume/center?lang=c",
    ) -> dict:
        return run_async_blocking(
            self._sync_master_resume(
                profile_dir,
                master_resume,
                headless=headless,
                target_url=target_url,
            )
        )

    async def _import_resume_snapshot(
        self,
        profile_dir: str,
        *,
        headless: bool | None = None,
        target_url: str = "https://www.51job.com/resume/center?lang=c",
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
                    raise RuntimeError("前程无忧简历浏览器已启动，但未能连接到调试端口。")
                browser = await playwright.chromium.connect_over_cdp(ws_endpoint)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()

            await context.add_init_script(STEALTH_INIT_SCRIPT)
            await context.add_init_script(LAODENG_INIT_SCRIPT)
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(target_url, wait_until="domcontentloaded")
                with contextlib.suppress(Exception):
                    await page.wait_for_timeout(1200)
                if await self.is_login_required(page):
                    raise RuntimeError("前程无忧当前未登录，请先在账号配置浏览器中完成登录后再导入。")
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

    async def _sync_master_resume(
        self,
        profile_dir: str,
        master_resume: dict,
        *,
        headless: bool | None = None,
        target_url: str = "https://www.51job.com/resume/center?lang=c",
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
                    raise RuntimeError("前程无忧简历同步浏览器已启动，但未能连接到调试端口。")
                browser = await playwright.chromium.connect_over_cdp(ws_endpoint)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()

            await context.add_init_script(STEALTH_INIT_SCRIPT)
            await context.add_init_script(LAODENG_INIT_SCRIPT)
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(target_url, wait_until="domcontentloaded")
                with contextlib.suppress(Exception):
                    await page.wait_for_timeout(1200)
                if await self.is_login_required(page):
                    raise RuntimeError("前程无忧当前未登录，请先在账号配置浏览器中完成登录后再同步。")

                sync_result = await page.evaluate(
                    """async ({ masterResume }) => {
                        const api = window.$nuxt?.$api?.resumeApi;
                        if (!api) {
                            throw new Error('前程无忧页面接口未加载完成。');
                        }

                        const normalize = (value) => String(value || '').trim().toLowerCase();
                        const normalizeSpace = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                        const unique = (items) => {
                            const result = [];
                            const seen = new Set();
                            for (const item of items || []) {
                                const normalized = normalizeSpace(item);
                                if (!normalized) continue;
                                const key = normalized.toLowerCase();
                                if (seen.has(key)) continue;
                                seen.add(key);
                                result.push(normalized);
                            }
                            return result;
                        };
                        const splitLocations = (value) => unique(
                            String(value || '')
                                .split(/[,+，\\/\\s]+/)
                                .map((item) => item.trim())
                                .filter(Boolean)
                        );
                        const flattenFunctionItems = (items, collector = []) => {
                            for (const item of items || []) {
                                collector.push(item);
                                if (Array.isArray(item.items) && item.items.length) {
                                    flattenFunctionItems(item.items, collector);
                                }
                            }
                            return collector;
                        };
                        const collectAreaItems = (groups) => {
                            const result = [];
                            for (const group of groups || []) {
                                for (const item of group.items || []) {
                                    result.push(item);
                                }
                            }
                            return result;
                        };
                        const salaryText = normalizeSpace(masterResume?.job_preferences?.salary || '');
                        const salaryMonthMatch = salaryText.match(/(\\d+)\\s*(?:个?月|薪)/);
                        const salaryMonth = salaryMonthMatch ? Number(salaryMonthMatch[1]) : null;
                        const parseSalaryPart = (value) => {
                            const match = String(value || '').match(/\\d+(?:\\.\\d+)?/);
                            if (!match) return null;
                            const num = Number(match[0]);
                            if (/[万wW]/.test(value || '')) return Math.round(num * 10000);
                            if (/[千kK]/.test(value || '')) return Math.round(num * 1000);
                            return num > 1000 ? Math.round(num) : Math.round(num * 1000);
                        };
                        const parseSalaryRange = (value) => {
                            if (!value) return { min: null, max: null };
                            const cleaned = String(value).split('·')[0].replace(/×/g, '').trim();
                            const parts = cleaned.split('-').map((item) => item.trim()).filter(Boolean);
                            if (!parts.length) return { min: null, max: null };
                            if (parts.length === 1) {
                                const single = parseSalaryPart(parts[0]);
                                return { min: single, max: single };
                            }
                            let [left, right] = parts;
                            const rightUnit = right.match(/[万wW千kK]/)?.[0] || '';
                            const leftUnit = left.match(/[万wW千kK]/)?.[0] || '';
                            if (!leftUnit && rightUnit) left += rightUnit;
                            if (!right.match(/[万wW千kK]/) && leftUnit) right += leftUnit;
                            return { min: parseSalaryPart(left), max: parseSalaryPart(right) };
                        };

                        const resumeResp = await api.resumesListNew({ isEnglish: false, useStudentIntentionSort: true });
                        const resumeItem = resumeResp?.resultbody?.items?.[0];
                        if (!resumeItem?.resumeId) {
                            throw new Error('未能读取前程无忧简历标识。');
                        }

                        const messages = [];
                        const summary = normalizeSpace(masterResume?.summary || '');
                        if (summary) {
                            try {
                                await api.editSelfIntroduction(
                                    resumeItem.resumeId,
                                    { selfIntroduction: summary, isEnglish: false }
                                );
                                messages.push('个人优势已同步');
                            } catch (error) {
                                messages.push('个人优势跳过');
                            }
                        } else {
                            messages.push('个人优势跳过');
                        }

                        const currentIntention = resumeItem.intentions?.[0];
                        if (currentIntention?.id) {
                            const jobPreferences = masterResume?.job_preferences || {};
                            let expectFunction = currentIntention.expectFunction || '';
                            let expectFunctionName = currentIntention.expectFunctionName || '';
                            let functionUpdated = false;
                            const desiredRole = normalizeSpace(jobPreferences.desired_role || '');
                            if (desiredRole) {
                                try {
                                    const assocResp = await api.getAssociateFuntype({
                                        keyword: desiredRole,
                                        isEnglish: false,
                                        isCampus: true,
                                    });
                                    const flatItems = flattenFunctionItems(assocResp?.resultbody || []);
                                    const exact = flatItems.find((item) => normalize(item.value) === normalize(desiredRole));
                                    const fuzzy = exact || flatItems.find((item) => normalize(item.value).includes(normalize(desiredRole)));
                                    if (fuzzy?.code && fuzzy?.value) {
                                        expectFunction = fuzzy.code;
                                        expectFunctionName = fuzzy.value;
                                        functionUpdated = true;
                                    }
                                } catch (error) {
                                    // Keep current function when dictionary search is unavailable.
                                }
                            }

                            let expectArea = currentIntention.expectArea || '';
                            let areaUpdated = false;
                            const locations = splitLocations(jobPreferences.locations || '');
                            if (locations.length) {
                                const areaCodes = [];
                                for (const location of locations.slice(0, 3)) {
                                    try {
                                        const areaResp = await api.getAreaDict({ keyword: location });
                                        const areaItems = collectAreaItems(areaResp?.resultbody || []);
                                        const exactArea = areaItems.find((item) => normalize(item.value) === normalize(location));
                                        const fuzzyArea = exactArea || areaItems.find((item) => normalize(item.value).includes(normalize(location)));
                                        if (fuzzyArea?.code) {
                                            areaCodes.push(fuzzyArea.code);
                                        }
                                    } catch (error) {
                                        // Ignore one-off area lookup failures and keep current areas.
                                    }
                                }
                                if (areaCodes.length) {
                                    expectArea = unique(areaCodes).join(',');
                                    areaUpdated = true;
                                }
                            }

                            const salaryRange = parseSalaryRange(jobPreferences.salary || '');
                            const seekTypeMap = { '全职': '0', '兼职': '1', '实习': '2' };
                            const nextSeekType = seekTypeMap[jobPreferences.job_type] ?? String(currentIntention.seekType ?? '0');
                            const nextSalaryMonth = salaryMonth || Number(currentIntention.salaryMonth || 12) || 12;
                            const minSalary = salaryRange.min || Number(currentIntention.minSalary || 0) || null;
                            const maxSalary = salaryRange.max || Number(currentIntention.maxSalary || 0) || null;

                            await api.editIntention(
                                currentIntention.id,
                                {
                                    salaryType: 1,
                                    expectArea,
                                    expectIndustry: currentIntention.expectIndustry || null,
                                    expectFunction,
                                    expectFunctionName,
                                    seekType: nextSeekType,
                                    maxSalary,
                                    minSalary,
                                    isEnglish: false,
                                    salaryMonth: nextSalaryMonth,
                                    intentionPreference: currentIntention.intentionPreference || {},
                                }
                            );

                            const intentionChanges = [];
                            if (functionUpdated) intentionChanges.push('职位');
                            if (areaUpdated) intentionChanges.push('城市');
                            if (salaryRange.min || salaryRange.max) intentionChanges.push('薪资');
                            if (jobPreferences.job_type) intentionChanges.push('工作类型');
                            messages.push(
                                intentionChanges.length
                                    ? `求职意向已同步(${intentionChanges.join('、')})`
                                    : '求职意向已同步(沿用当前职位/城市，仅校验保存)'
                            );
                        } else {
                            messages.push('求职意向跳过');
                        }

                        const personalSkill = resumeItem.personalSkills?.[0];
                        const masterSkills = unique(masterResume?.skills || []);
                        if (personalSkill?.questions?.length && masterSkills.length) {
                            const masterSkillSet = new Set(masterSkills.map((item) => normalize(item)));
                            const questions = personalSkill.questions.map((question) => {
                                const matched = (question.answers || []).filter((answer) => masterSkillSet.has(normalize(answer.title)));
                                const fallback = matched.length
                                    ? matched
                                    : (question.answers || []).filter((answer) => answer.isSelected);
                                return {
                                    id: String(question.id),
                                    answers: fallback.map((answer) => ({
                                        code: String(answer.code),
                                        isSelected: true,
                                        title: answer.title,
                                    })),
                                };
                            });
                            await api.perSkillEdit({
                                isEnglish: false,
                                scene: '简历中心',
                                policyName: personalSkill.policyName,
                                policyId: personalSkill.policyId,
                                function: personalSkill.function,
                                questions,
                            });
                            const selectedCount = questions.reduce((sum, item) => sum + item.answers.length, 0);
                            messages.push(`技能经验已同步(${selectedCount}项)`);
                        } else if (personalSkill?.questions?.length) {
                            messages.push('技能经验跳过');
                        } else {
                            messages.push('技能经验跳过');
                        }

                        return {
                            success: true,
                            message: messages.join('；'),
                        };
                    }""",
                    {"masterResume": master_resume},
                )
                return dict(sync_result or {"success": True, "message": "前程无忧同步完成"})
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

        def is_date_line(value: str) -> bool:
            return bool(re.search(r"\d{4}[./-]\d{1,2}\s*-\s*(?:\d{4}[./-]\d{1,2}|至今)", value))

        def find_index(candidates: list[str], start_at: int = 0) -> int | None:
            for idx in range(start_at, len(normalized_lines)):
                if normalized_lines[idx] in candidates:
                    return idx
            return None

        content_start = find_index(["预览"]) or 0

        def section_slice(name: str, next_names: list[str]) -> list[str]:
            start_idx = find_index([name], content_start)
            if start_idx is None:
                return []
            start = start_idx + 1
            end = len(normalized_lines)
            for next_name in next_names:
                next_idx = find_index([next_name], start)
                if next_idx is not None:
                    end = min(end, next_idx)
            return normalized_lines[start:end]

        summary_lines = section_slice("个人优势", ["求职意向"])
        preference_lines = section_slice("求职意向", ["技能经验"])
        skill_lines = section_slice("技能经验", ["工作经历"])
        work_lines = section_slice("工作经历", ["项目经历"])
        project_lines = section_slice("项目经历", ["教育经历"])
        education_lines = section_slice("教育经历", ["在校荣誉/职务"])
        honor_lines = section_slice("在校荣誉/职务", ["语言能力"])
        language_lines = section_slice("语言能力", ["个人作品"])
        certificate_lines = section_slice("资格证书", ["自定义添加", "附件管理"])
        attachment_lines = section_slice("附件管理", ["简历设置", "小程序"])

        name = ""
        headline = ""
        contact_summary = ""
        for idx, value in enumerate(normalized_lines):
            if value == "预览" and idx + 9 < len(normalized_lines):
                name = normalized_lines[idx + 1]
                headline = " / ".join(normalized_lines[idx + 3 : idx + 8])
                contact_summary = " ".join(normalized_lines[idx + 8 : idx + 11])
                break

        contact_tokens = [token for token in contact_summary.split() if token]
        wechat_id = ""
        for token in contact_tokens:
            if "@" not in token and not re.search(r"\d{3}\*+\d{4}", token):
                wechat_id = token
                break

        job_preferences = {
            "desired_role": preference_lines[0] if len(preference_lines) > 0 else "",
            "job_type": preference_lines[1] if len(preference_lines) > 1 else "",
            "salary": preference_lines[2] if len(preference_lines) > 2 else "",
            "locations": preference_lines[3] if len(preference_lines) > 3 else "",
            "industry": preference_lines[4] if len(preference_lines) > 4 else "",
            "raw_tokens": preference_lines,
        }

        work_experiences: list[dict] = []
        idx = 0
        while idx + 3 < len(work_lines):
            company = work_lines[idx]
            title = work_lines[idx + 1]
            employment_type = work_lines[idx + 2]
            date_range = work_lines[idx + 3]
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
                if len(current) <= 24 and "\n" not in current and "：" not in current:
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
        while idx + 1 < len(project_lines):
            name_line = project_lines[idx]
            date_range = project_lines[idx + 1]
            if not is_date_line(date_range):
                idx += 1
                continue
            idx += 2
            description_parts: list[str] = []
            while idx < len(project_lines):
                current = project_lines[idx]
                if idx + 1 < len(project_lines) and is_date_line(project_lines[idx + 1]):
                    break
                description_parts.append(current)
                idx += 1
            project_experiences.append(
                {"name": name_line, "date_range": date_range, "role": "", "description": "\n".join(description_parts).strip()}
            )

        education_experiences: list[dict] = []
        idx = 0
        while idx + 4 < len(education_lines):
            school = education_lines[idx]
            date_range = education_lines[idx + 1]
            major = education_lines[idx + 2]
            degree = education_lines[idx + 3]
            study_mode = education_lines[idx + 4]
            if not is_date_line(date_range):
                idx += 1
                continue
            education_experiences.append(
                {
                    "school": school,
                    "date_range": date_range,
                    "major": major,
                    "degree": degree,
                    "study_mode": study_mode,
                }
            )
            idx += 5

        attachments: list[dict] = []
        for idx, value in enumerate(attachment_lines):
            if value.endswith(".pdf") or value.endswith(".doc") or value.endswith(".docx"):
                attachments.append(
                    {"name": value, "meta": attachment_lines[idx + 1] if idx + 1 < len(attachment_lines) else ""}
                )

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
            "certificates": certificate_lines,
            "skills": skill_lines,
            "languages": language_lines,
            "attachments": attachments,
            "raw_snapshot": {"lines": normalized_lines, "honors": honor_lines},
        }
