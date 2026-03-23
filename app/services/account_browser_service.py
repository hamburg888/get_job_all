import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
import sys

from app.core.config import settings
from app.models.entities import Account, Platform

logger = logging.getLogger(__name__)


@dataclass
class BrowserConfigState:
    thread: threading.Thread
    stop_event: threading.Event
    started_event: threading.Event
    error: str | None = None


class AccountBrowserService:
    def __init__(self) -> None:
        self._sessions: dict[int, BrowserConfigState] = {}
        self._lock = threading.Lock()

    def open_for_account(self, account: Account) -> dict:
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
                    args=(account.id, account.platform, account.profile_dir, stop_event, started_event),
                    daemon=True,
                ),
                stop_event=stop_event,
                started_event=started_event,
            )
            self._sessions[account.id] = state
            state.thread.start()

        started_event.wait(timeout=15)
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

        state.thread.join(timeout=5)
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
    ) -> None:
        state = self._sessions.get(account_id)
        try:
            entry_url = self._platform_entry_url(Platform(platform_value))
            profile_path = Path(profile_dir)
            if not profile_path.is_absolute():
                profile_path = Path.cwd() / profile_path
            profile_path.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                loop = asyncio.ProactorEventLoop()
                try:
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(
                        self._run_browser_session_async(account_id, entry_url, profile_path, stop_event, started_event)
                    )
                finally:
                    loop.close()
                    asyncio.set_event_loop(None)
            else:
                asyncio.run(
                    self._run_browser_session_async(account_id, entry_url, profile_path, stop_event, started_event)
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Open browser config failed for account %s", account_id)
            if state:
                state.error = str(exc)
            started_event.set()
        finally:
            with self._lock:
                self._sessions.pop(account_id, None)

    async def _run_browser_session_async(
        self,
        account_id: int,
        entry_url: str,
        profile_path: Path,
        stop_event: threading.Event,
        started_event: threading.Event,
    ) -> None:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                headless=settings.browser_headless,
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                try:
                    await page.goto(entry_url, wait_until="domcontentloaded", timeout=60000)
                except Exception:  # noqa: BLE001
                    logger.warning("Open browser config page failed for account %s", account_id, exc_info=True)
                started_event.set()
                while not stop_event.is_set():
                    try:
                        if len(context.pages) == 0:
                            break
                    except Exception:  # noqa: BLE001
                        break
                    await asyncio.sleep(0.5)
            finally:
                try:
                    await context.close()
                except Exception:  # noqa: BLE001
                    logger.info("Browser config context already closed for account %s", account_id)

    @staticmethod
    def _platform_entry_url(platform: Platform) -> str:
        if platform == Platform.ZHILIAN:
            return settings.zhilian_entry_url
        return settings.boss_entry_url


account_browser_service = AccountBrowserService()
