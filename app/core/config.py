import os
import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _is_desktop_runtime() -> bool:
    executable_name = Path(sys.argv[0]).name.lower()
    return bool(getattr(sys, "frozen", False) or executable_name == "run_desktop.py")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _desktop_data_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "JobHelpDesktop"
    return Path.home() / "AppData" / "Local" / "JobHelpDesktop"


def _resource_root() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass).resolve()
        return Path(sys.executable).resolve().parent
    return _project_root()


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def _default_database_url() -> str:
    if _is_desktop_runtime():
        return _sqlite_url(_desktop_data_root() / "data" / "job_help.db")
    return _sqlite_url(_project_root() / "job_help.db")


def _default_frontend_dist_dir() -> str:
    return str((_resource_root() / "frontend_dist").resolve())


def _default_browser_profile_root() -> str:
    if _is_desktop_runtime():
        return str((_desktop_data_root() / "browser_profiles").resolve())
    return str((_project_root() / "browser_profiles").resolve())


def _default_preview_frame_root() -> str:
    if _is_desktop_runtime():
        return str((_desktop_data_root() / "preview_frames").resolve())
    return str((_project_root() / "preview_frames").resolve())


def _default_upload_root() -> str:
    if _is_desktop_runtime():
        return str((_desktop_data_root() / "uploads").resolve())
    return str((_project_root() / "uploads").resolve())


def _default_log_root() -> str:
    if _is_desktop_runtime():
        return str((_desktop_data_root() / "logs").resolve())
    return str((_project_root() / "logs").resolve())


def _default_desktop_db_template_path() -> str:
    return str((_resource_root() / "desktop_assets" / "job_help.template.db").resolve())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Job Help API"
    desktop_runtime: bool = Field(default_factory=_is_desktop_runtime)
    database_url: str = Field(default_factory=_default_database_url)
    bootstrap_demo_data_enabled: bool = False
    automation_mode: str = "playwright"
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    log_level: str = "INFO"
    log_root: str = Field(default_factory=_default_log_root)
    liepin_entry_url: str = "https://www.liepin.com/zhaopin/"
    liepin_login_url: str = "https://www.liepin.com/"
    zhilian_entry_url: str = "https://www.zhaopin.com/sou/jl763/p1"
    qiancheng_entry_url: str = "https://we.51job.com/pc/search"
    qiancheng_login_url: str = "https://login.51job.com/login.php"
    boss_entry_url: str = "https://www.zhipin.com/"
    boss_jobs_url: str = "https://www.zhipin.com/web/geek/jobs"
    boss_chat_url: str = "https://www.zhipin.com/web/geek/chat"
    boss_login_url: str = "https://www.zhipin.com/web/user/?ka=header-login"
    frontend_dist_dir: str = Field(default_factory=_default_frontend_dist_dir)
    desktop_host: str = "127.0.0.1"
    desktop_port: int = 8000
    desktop_auto_open_browser: bool = False
    desktop_window_title: str = "Job Help Desktop"
    desktop_window_width: int = 1440
    desktop_window_height: int = 920
    desktop_window_min_width: int = 1180
    desktop_window_min_height: int = 760
    desktop_db_template_path: str = Field(default_factory=_default_desktop_db_template_path)
    browser_profile_root: str = Field(default_factory=_default_browser_profile_root)
    browser_headless: bool = False
    browser_fake_headless: bool = True
    browser_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/134.0.0.0 Safari/537.36"
    )
    preview_frame_root: str = Field(default_factory=_default_preview_frame_root)
    upload_root: str = Field(default_factory=_default_upload_root)


settings = Settings()
