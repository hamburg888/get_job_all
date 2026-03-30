from pathlib import Path

from app.core.config import settings


def resolve_profile_dir(profile_dir: str) -> str:
    profile_path = Path(profile_dir)
    if profile_path.is_absolute():
        return str(profile_path.resolve())

    browser_root = Path(settings.browser_profile_root).resolve()
    project_root = browser_root.parent
    return str((project_root / profile_path).resolve())
