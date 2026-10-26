import asyncio
import contextlib
import json
import logging
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
import sys

from app.automation.browser_factory import (
    LAODENG_INIT_SCRIPT,
    STEALTH_INIT_SCRIPT,
    build_visible_login_launch_options,
)
from app.core.config import settings
from app.models.entities import Account, Platform
from app.services.profile_path_resolver import resolve_profile_dir

logger = logging.getLogger(__name__)


@dataclass
class BrowserConfigState:
    thread: threading.Thread
    stop_event: threading.Event
    started_event: threading.Event
    error: str | None = None
    chrome_pid: int | None = None
    debug_port: int | None = None
    platform: str | None = None
    profile_dir: str | None = None


class AccountBrowserService:
    def __init__(self) -> None:
        self._sessions: dict[int, BrowserConfigState] = {}
        self._lock = threading.Lock()
        self._instrumented_pages: set[int] = set()

    @staticmethod
    def _normalize_profile_dir(profile_dir: str) -> str:
        return resolve_profile_dir(profile_dir)

    def is_profile_occupied(self, profile_dir: str) -> bool:
        normalized_profile_dir = self._normalize_profile_dir(profile_dir)
        with self._lock:
            return any(
                state.thread.is_alive() and state.profile_dir == normalized_profile_dir
                for state in self._sessions.values()
            )

    def open_for_account(self, account: Account, *, site: bool = False) -> dict:
        with self._lock:
            current = self._sessions.get(account.id)
            if current and current.thread.is_alive():
                return {
                    "account_id": account.id,
                    "active": True,
                    "message": "配置浏览器已打开，可直接继续登录配置。",
                }

            stop_event = threading.Event()
            started_event = threading.Event()
            state = BrowserConfigState(
                thread=threading.Thread(
                    target=self._run_browser_session,
                    args=(account.id, account.platform, account.profile_dir, stop_event, started_event, site),
                    daemon=True,
                ),
                stop_event=stop_event,
                started_event=started_event,
                platform=account.platform,
                profile_dir=self._normalize_profile_dir(account.profile_dir),
            )
            self._sessions[account.id] = state
            state.thread.start()

        started_event.wait(timeout=20)
        if state.error:
            self.close_for_account(account.id)
            raise RuntimeError(state.error)
        return {
            "account_id": account.id,
            "active": True,
            "message": "配置浏览器已打开，请在新窗口完成登录配置。",
        }

    def close_for_account(self, account_id: int) -> dict:
        with self._lock:
            state = self._sessions.get(account_id)
            if not state:
                return {
                    "account_id": account_id,
                    "active": False,
                    "message": "未检测到活动浏览器，可能已经手动关闭。",
                }
            state.stop_event.set()

        state.thread.join(timeout=8)
        with self._lock:
            self._sessions.pop(account_id, None)
        return {
            "account_id": account_id,
            "active": False,
            "message": "配置浏览器关闭指令已处理。",
        }

    def _run_browser_session(
        self,
        account_id: int,
        platform_value: str,
        profile_dir: str,
        stop_event: threading.Event,
        started_event: threading.Event,
        site: bool = False,
    ) -> None:
        state = self._sessions.get(account_id)
        chrome_pid: int | None = None
        try:
            platform = Platform(platform_value)
            entry_url = self._platform_entry_url(platform)
            if site:
                entry_url = {
                    Platform.BOSS: settings.boss_jobs_url,
                    Platform.LIEPIN: settings.liepin_entry_url,
                    Platform.YUPAO: settings.yupao_entry_url,
                    Platform.ZHILIAN: settings.zhilian_entry_url,
                    Platform.QIANCHENG: settings.qiancheng_entry_url,
                }[platform]
            profile_path = Path(self._normalize_profile_dir(profile_dir))
            profile_path.mkdir(parents=True, exist_ok=True)

            chrome_path = self._resolve_chrome_executable()
            debug_port = self._pick_free_port() if platform != Platform.BOSS else None
            chrome_pid = self._launch_external_chrome(chrome_path, profile_path, debug_port, entry_url)

            if state:
                state.chrome_pid = chrome_pid
                state.debug_port = debug_port

            if platform == Platform.BOSS:
                started_event.set()
                self._wait_browser_closed_or_stop(chrome_pid, stop_event)
                return

            ws_endpoint = self._wait_for_cdp_endpoint(debug_port, timeout=20) if debug_port else None
            if not ws_endpoint:
                raise RuntimeError("外部 Chrome 已启动，但未能打开调试端口。")

            if sys.platform == "win32":
                loop = asyncio.ProactorEventLoop()
                try:
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(
                        self._run_browser_session_async(
                            account_id,
                            entry_url,
                            ws_endpoint,
                            stop_event,
                            started_event,
                        )
                    )
                finally:
                    loop.close()
                    asyncio.set_event_loop(None)
            else:
                asyncio.run(
                    self._run_browser_session_async(
                        account_id,
                        entry_url,
                        ws_endpoint,
                        stop_event,
                        started_event,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Open browser config failed for account %s", account_id)
            if state:
                state.error = str(exc)
            started_event.set()
        finally:
            self._terminate_chrome_process(chrome_pid)
            with self._lock:
                self._sessions.pop(account_id, None)

    def _wait_browser_closed_or_stop(self, chrome_pid: int | None, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            if not self._is_process_alive(chrome_pid):
                break
            time.sleep(0.5)

    async def _run_browser_session_async(
        self,
        account_id: int,
        entry_url: str,
        ws_endpoint: str,
        stop_event: threading.Event,
        started_event: threading.Event,
    ) -> None:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(ws_endpoint)
            try:
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                await context.add_init_script(STEALTH_INIT_SCRIPT)
                await context.add_init_script(LAODENG_INIT_SCRIPT)
                self._attach_context_debug_listeners(account_id, context)

                existing_pages = list(context.pages)
                if existing_pages:
                    page = self._select_best_existing_page(existing_pages, entry_url)
                    self._attach_page_debug_listeners(account_id, page, source="external_chrome_existing")
                else:
                    try:
                        page = await context.new_page()
                    except Exception as exc:  # noqa: BLE001
                        raise RuntimeError("外部 Chrome 已连接，但无法创建或接管标签页。") from exc
                    self._attach_page_debug_listeners(account_id, page, source="external_chrome_new")
                last_snapshot = self._log_context_pages(account_id, context, "browser_config_open_before_goto")

                try:
                    await page.goto(entry_url, wait_until="domcontentloaded", timeout=60000)
                except Exception:  # noqa: BLE001
                    logger.warning("Open browser config page failed for account %s", account_id, exc_info=True)

                if "zhipin.com" in entry_url:
                    await self._prepare_boss_login_page(account_id, page)

                await self._close_redundant_startup_pages(account_id, existing_pages, page)
                with contextlib.suppress(Exception):
                    await page.bring_to_front()
                last_snapshot = self._log_context_pages(account_id, context, "browser_config_open_after_goto", last_snapshot)
                started_event.set()

                while not stop_event.is_set():
                    try:
                        if len(context.pages) == 0:
                            break
                    except Exception:  # noqa: BLE001
                        break
                    page = await self._ensure_target_page(account_id, context, page, entry_url)
                    last_snapshot = self._log_context_pages(account_id, context, "browser_config_heartbeat", last_snapshot)
                    await asyncio.sleep(0.5)
            finally:
                with contextlib.suppress(Exception):
                    await browser.close()

    async def _close_redundant_startup_pages(self, account_id: int, existing_pages: list, target_page) -> None:
        await asyncio.sleep(0.8)
        for page in existing_pages:
            if page == target_page:
                continue
            with contextlib.suppress(Exception):
                page_url = (page.url or "").strip() or "<empty>"
                logger.info(
                    "Browser config account %s closing startup page: url=%s",
                    account_id,
                    page_url,
                )
                await page.close()

    async def _ensure_target_page(self, account_id: int, context, page, entry_url: str):
        try:
            if page.is_closed():
                logger.info("Browser config account %s target page closed unexpectedly, recreating", account_id)
                new_page = await context.new_page()
                self._attach_page_debug_listeners(account_id, new_page, source="recreated")
                await new_page.goto(entry_url, wait_until="domcontentloaded", timeout=60000)
                if "zhipin.com" in entry_url:
                    await self._prepare_boss_login_page(account_id, new_page)
                with contextlib.suppress(Exception):
                    await new_page.bring_to_front()
                return new_page
        except Exception:  # noqa: BLE001
            logger.warning("Browser config account %s failed to inspect target page", account_id, exc_info=True)
            return page

        current_url = ((page.url or "").strip().lower()) if getattr(page, "url", None) is not None else ""
        if current_url in {"", "about:blank"}:
            logger.info(
                "Browser config account %s target page became blank, reopening entry url=%s",
                account_id,
                entry_url,
            )
            with contextlib.suppress(Exception):
                await page.goto(entry_url, wait_until="domcontentloaded", timeout=60000)
            if "zhipin.com" in entry_url:
                with contextlib.suppress(Exception):
                    await self._prepare_boss_login_page(account_id, page)
            with contextlib.suppress(Exception):
                await page.bring_to_front()
        return page

    async def _prepare_boss_login_page(self, account_id: int, page) -> None:
        current_url = (page.url or "").strip()
        logger.info("Browser config account %s prepare boss login page: url=%s", account_id, current_url)
        try:
            qr_switch = page.locator(".btn-sign-switch.ewm-switch").first
            if await qr_switch.count() and await qr_switch.is_visible():
                await qr_switch.click()
                logger.info("Browser config account %s switched boss login to QR mode via primary selector", account_id)
                await page.wait_for_timeout(800)
                return
        except Exception:  # noqa: BLE001
            logger.debug("Boss QR switch primary selector not available for account %s", account_id, exc_info=True)

        try:
            text_switch = page.get_by_text("APP扫码登录").first
            if await text_switch.count() and await text_switch.is_visible():
                await text_switch.click()
                logger.info("Browser config account %s switched boss login to QR mode via text selector", account_id)
                await page.wait_for_timeout(800)
                return
        except Exception:  # noqa: BLE001
            logger.debug("Boss QR switch text selector not available for account %s", account_id, exc_info=True)

        try:
            legacy_switch = page.locator("li.sign-switch-tip").first
            if await legacy_switch.count() and await legacy_switch.is_visible():
                await legacy_switch.click()
                logger.info("Browser config account %s switched boss login to QR mode via legacy selector", account_id)
                await page.wait_for_timeout(800)
                return
        except Exception:  # noqa: BLE001
            logger.debug("Boss QR switch legacy selector not available for account %s", account_id, exc_info=True)

    def _attach_context_debug_listeners(self, account_id: int, context) -> None:
        def on_page(page) -> None:
            self._attach_page_debug_listeners(account_id, page, source="new_page")
            logger.info(
                "Browser config account %s new page created: url=%s",
                account_id,
                getattr(page, "url", ""),
            )

        context.on("page", on_page)

    def _attach_page_debug_listeners(self, account_id: int, page, *, source: str) -> None:
        page_key = id(page)
        if page_key in self._instrumented_pages:
            return
        self._instrumented_pages.add(page_key)

        logger.info(
            "Browser config account %s attach page listeners: source=%s url=%s",
            account_id,
            source,
            getattr(page, "url", ""),
        )

        def on_framenavigated(frame) -> None:
            if frame == page.main_frame:
                logger.info(
                    "Browser config account %s main frame navigated: url=%s",
                    account_id,
                    frame.url,
                )

        def on_load() -> None:
            logger.info(
                "Browser config account %s page load: url=%s",
                account_id,
                getattr(page, "url", ""),
            )

        def on_domcontentloaded() -> None:
            logger.info(
                "Browser config account %s domcontentloaded: url=%s",
                account_id,
                getattr(page, "url", ""),
            )

        def on_close() -> None:
            logger.info("Browser config account %s page closed", account_id)

        page.on("framenavigated", on_framenavigated)
        page.on("load", on_load)
        page.on("domcontentloaded", on_domcontentloaded)
        page.on("close", on_close)

    def _log_context_pages(self, account_id: int, context, stage: str, last_snapshot: tuple[str, ...] | None = None) -> tuple[str, ...]:
        with contextlib.suppress(Exception):
            urls = tuple(((page.url or "").strip() or "<empty>") for page in context.pages)
            if urls != (last_snapshot or ()):
                logger.info(
                    "Browser config account %s pages snapshot [%s]: %s",
                    account_id,
                    stage,
                    list(urls),
                )
            return urls
        return last_snapshot or ()

    @staticmethod
    def _select_best_existing_page(pages: list, entry_url: str):
        entry_host = ""
        with contextlib.suppress(Exception):
            from urllib.parse import urlparse

            entry_host = urlparse(entry_url).netloc.lower()

        def page_url(page) -> str:
            with contextlib.suppress(Exception):
                return (page.url or "").strip()
            return ""

        regular_pages = [page for page in pages if not page_url(page).startswith("chrome://")]
        if entry_host:
            for page in regular_pages:
                if entry_host in page_url(page).lower():
                    return page
        if regular_pages:
            return regular_pages[0]
        return pages[0]

    def _launch_external_chrome(self, chrome_path: str, profile_path: Path, debug_port: int | None, entry_url: str) -> int:
        launch_options = build_visible_login_launch_options()
        args = [
            f"--user-data-dir={profile_path}",
            "--new-window",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--disable-infobars",
            *launch_options["args"],
            entry_url,
        ]
        if debug_port:
            args.insert(0, f"--remote-debugging-port={debug_port}")
        quoted_file = chrome_path.replace("'", "''")
        quoted_args = "'" + subprocess.list2cmdline(args).replace("'", "''") + "'"
        command = (
            f"$p = Start-Process -FilePath '{quoted_file}' "
            f"-ArgumentList @({quoted_args}) -PassThru; "
            "Write-Output $p.Id"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            capture_output=True,
            text=True,
            check=True,
        )
        pid_text = (result.stdout or "").strip().splitlines()[-1]
        logger.info("Started external Chrome for browser config: pid=%s port=%s", pid_text, debug_port)
        return int(pid_text)

    def _wait_for_cdp_endpoint(self, debug_port: int, timeout: int = 20) -> str | None:
        deadline = time.time() + timeout
        url = f"http://127.0.0.1:{debug_port}/json/version"
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    endpoint = payload.get("webSocketDebuggerUrl")
                    if endpoint:
                        return endpoint
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        return None

    def _terminate_chrome_process(self, chrome_pid: int | None) -> None:
        if not chrome_pid:
            return
        # Ask Chrome to close normally so cookies and session data reach disk.
        if sys.platform == "win32" and self._is_process_alive(chrome_pid):
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def close_window(hwnd, _):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == chrome_pid:
                    user32.PostMessageW(hwnd, 0x0010, 0, 0)
                return True
            user32.EnumWindows(callback_type(close_window), 0)
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                if not self._is_process_alive(chrome_pid):
                    return
                time.sleep(0.2)
        with contextlib.suppress(Exception):
            subprocess.run(
                ["taskkill", "/PID", str(chrome_pid), "/T", "/F"],
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                capture_output=True,
                text=True,
            )

    @staticmethod
    def _is_process_alive(chrome_pid: int | None) -> bool:
        if not chrome_pid:
            return False
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {chrome_pid}"],
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            capture_output=True,
            text=True,
        )
        return str(chrome_pid) in (result.stdout or "")

    @staticmethod
    def _pick_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    @staticmethod
    def _platform_entry_url(platform: Platform) -> str:
        if platform == Platform.LIEPIN:
            return settings.liepin_login_url
        if platform == Platform.YUPAO:
            return settings.yupao_login_url
        if platform == Platform.ZHILIAN:
            return settings.zhilian_entry_url
        if platform == Platform.QIANCHENG:
            return settings.qiancheng_login_url
        return settings.boss_login_url

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


account_browser_service = AccountBrowserService()
