from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Job Help API"
    database_url: str = "sqlite:///./job_help.db"
    automation_mode: str = "playwright"
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    log_level: str = "INFO"
    zhilian_entry_url: str = "https://www.zhaopin.com/sou/jl763/p1"
    boss_entry_url: str = "https://www.zhipin.com/"
    browser_profile_root: str = "./browser_profiles"
    browser_headless: bool = False
    browser_fake_headless: bool = True
    browser_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/134.0.0.0 Safari/537.36"
    )
    preview_frame_root: str = "./preview_frames"


settings = Settings()
