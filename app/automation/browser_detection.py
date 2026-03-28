import shutil
from pathlib import Path


def candidate_browser_paths() -> list[str]:
    return [
        value
        for value in [
            shutil.which("chrome"),
            shutil.which("chrome.exe"),
            shutil.which("msedge"),
            shutil.which("msedge.exe"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Google\Chrome Beta\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome Beta\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
        if value
    ]


def find_system_browser() -> str | None:
    for candidate in candidate_browser_paths():
        if Path(candidate).exists():
            return candidate
    return None
