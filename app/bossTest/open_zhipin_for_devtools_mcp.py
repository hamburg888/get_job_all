import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


TARGET_URL = os.getenv("ZHIPIN_TARGET_URL", "https://www.zhipin.com/")
WAIT_SECONDS = int(os.getenv("ZHIPIN_WAIT_SECONDS", "300"))
REMOTE_DEBUGGING_PORT = int(os.getenv("CHROME_REMOTE_DEBUGGING_PORT", "9222"))
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


def main() -> None:
    chrome_path = find_chrome_path()
    user_data_dir = Path(tempfile.mkdtemp(prefix="boss-zhipin-devtools-mcp-"))
    chrome_process: subprocess.Popen | None = None

    try:
        chrome_process = subprocess.Popen(
            [
                str(chrome_path),
                f"--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={REMOTE_DEBUGGING_PORT}",
                f"--user-data-dir={user_data_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--start-maximized",
                "--lang=zh-CN",
                TARGET_URL,
            ]
        )
        print(f"Chrome 已启动: {TARGET_URL}")
        print(f"DevTools 调试地址: http://127.0.0.1:{REMOTE_DEBUGGING_PORT}")
        print(f"保持打开 {WAIT_SECONDS} 秒，供 chrome-devtools-mcp 接管。")
        time.sleep(WAIT_SECONDS)
    finally:
        if chrome_process is not None and chrome_process.poll() is None:
            chrome_process.kill()
            chrome_process.wait(timeout=10)
        shutil.rmtree(user_data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
