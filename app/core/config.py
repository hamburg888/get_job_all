from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Job Help API"
    database_url: str = "sqlite:///./job_help.db"
    automation_mode: str = "playwright"
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    log_level: str = "INFO"
    zhilian_entry_url: str = "https://www.zhaopin.com/sou/jl763/p1"
    browser_profile_root: str = "./browser_profiles"
    browser_headless: bool = False


settings = Settings()
