import ctypes
from pathlib import Path
import logging
import threading
import time

import httpx
import uvicorn
import webview

from app.automation.browser_detection import find_system_browser
from app.core.config import settings

logger = logging.getLogger(__name__)


def _browser_origin() -> str:
    host = settings.desktop_host.strip() or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{settings.desktop_port}"


def _show_native_message(title: str, message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x40 | 0x1000)
    except Exception:
        logger.warning("%s: %s", title, message)


def _warn_if_browser_missing() -> None:
    if find_system_browser():
        return
    _show_native_message(
        settings.desktop_window_title,
        "未检测到 Chrome 或 Edge。\n\n"
        "桌面管理界面仍可打开，但账号登录配置、自动投递和聊天功能依赖外部浏览器。\n"
        "请先安装 Chrome 或 Edge，再继续使用自动化功能。",
    )


class DesktopApi:
    def export_config(self, name: str, content: str) -> bool:
        paths = webview.windows[0].create_file_dialog(webview.FileDialog.SAVE, save_filename=Path(name).name, file_types=("JSON (*.json)",))
        if not paths:
            return False
        target = paths if isinstance(paths, str) else paths[0]
        Path(target).write_text(content, encoding="utf-8")
        return True


class DesktopUvicornServer(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        return


class DesktopServerThread:
    def __init__(self) -> None:
        config = uvicorn.Config(
            "app.main:app",
            host=settings.desktop_host,
            port=settings.desktop_port,
            reload=False,
            log_config=None,
        )
        self._server = DesktopUvicornServer(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True, name="job-help-desktop-server")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout=10)

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()


def _wait_for_server_ready(timeout_seconds: float = 30.0) -> bool:
    deadline = time.time() + timeout_seconds
    health_url = f"{_browser_origin()}/health"
    while time.time() < deadline:
        try:
            response = httpx.get(health_url, timeout=1.5, trust_env=False)
            if response.is_success:
                return True
        except Exception:
            pass
        time.sleep(0.35)
    return False


def main() -> int:
    _warn_if_browser_missing()
    server = DesktopServerThread()
    server.start()
    if not _wait_for_server_ready():
        server.stop()
        _show_native_message(settings.desktop_window_title, "本地桌面服务启动失败，请稍后重试。")
        return 1

    try:
        window = webview.create_window(
            settings.desktop_window_title,
            _browser_origin(),
            width=settings.desktop_window_width,
            height=settings.desktop_window_height,
            min_size=(settings.desktop_window_min_width, settings.desktop_window_min_height),
            confirm_close=True,
            text_select=True,
            js_api=DesktopApi(),
        )
        window.events.closed += lambda *_args: server.stop()
        webview.start(debug=False, private_mode=False, storage_path=str(Path(settings.browser_profile_root).parent / "desktop_webview"))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Desktop window start failed: %s", exc)
        _show_native_message(settings.desktop_window_title, f"桌面窗口启动失败：{exc}")
        server.stop()
        return 1

    server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
