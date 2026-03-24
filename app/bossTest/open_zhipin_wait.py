import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


TARGET_URL = os.getenv("ZHIPIN_TARGET_URL", "https://www.zhipin.com/")
WAIT_SECONDS = int(os.getenv("ZHIPIN_WAIT_SECONDS", "300"))
TAKEOVER_AFTER_SECONDS = int(os.getenv("ZHIPIN_TAKEOVER_AFTER_SECONDS", "10"))
CDP_CONNECT_TIMEOUT_MS = 30000
CHROME_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe",
)


def find_chrome_path() -> Path:
    for candidate in CHROME_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("未找到 Chrome，可先安装 Google Chrome 后再运行该脚本。")


def wait_for_devtools_port(user_data_dir: Path, timeout_seconds: int = 15) -> int:
    devtools_port_file = user_data_dir / "DevToolsActivePort"
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            if devtools_port_file.exists():
                lines = devtools_port_file.read_text(encoding="utf-8").splitlines()
                if lines and lines[0].strip().isdigit():
                    return int(lines[0].strip())
        except OSError:
            time.sleep(0.5)
            continue
        time.sleep(0.5)
    raise TimeoutError(f"Chrome 在 {timeout_seconds} 秒内未写出 DevTools 调试端口。")


def wait_for_cdp_http_ready(port: int, timeout_seconds: int = 15) -> None:
    deadline = time.time() + timeout_seconds
    version_url = f"http://127.0.0.1:{port}/json/version"
    while time.time() < deadline:
        try:
            with urlopen(version_url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.5)
    raise TimeoutError(f"Chrome 调试端口 {port} 在 {timeout_seconds} 秒内未就绪。")


def main() -> None:
    chrome_path = find_chrome_path()
    user_data_dir = Path(tempfile.mkdtemp(prefix="boss-zhipin-chrome-"))
    chrome_process: subprocess.Popen | None = None

    try:
        chrome_process = subprocess.Popen(
            [
                str(chrome_path),
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                f"--user-data-dir={user_data_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--start-maximized",
                "--lang=zh-CN",
                TARGET_URL,
            ]
        )

        remote_debugging_port = wait_for_devtools_port(user_data_dir)
        wait_for_cdp_http_ready(remote_debugging_port)

        initial_wait_seconds = max(0, min(TAKEOVER_AFTER_SECONDS, WAIT_SECONDS))
        remaining_wait_seconds = max(0, WAIT_SECONDS - initial_wait_seconds)

        time.sleep(initial_wait_seconds)

        if chrome_process.poll() is not None:
            raise RuntimeError("Chrome 在接管前已经退出，无法建立 Playwright 连接。")

        wait_for_cdp_http_ready(remote_debugging_port)

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(
                f"http://127.0.0.1:{remote_debugging_port}",
                timeout=CDP_CONNECT_TIMEOUT_MS,
            )
            try:
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else context.new_page()
                page.bring_to_front()
                page.evaluate(
                    """
                    () => {
                        alert('页面已被 Playwright 接管');
                    }
                    """
                )
                print(f"已成功接管 Chrome，正在等待 {remaining_wait_seconds} 秒...")
                time.sleep(remaining_wait_seconds)
            except PlaywrightError:
                time.sleep(remaining_wait_seconds)
            finally:
                print("等待结束，正在关闭 Playwright 连接...")
                browser.close()
    finally:
        if chrome_process is not None and chrome_process.poll() is None:
            chrome_process.kill()
            chrome_process.wait(timeout=10)
        shutil.rmtree(user_data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
