from pathlib import Path

from app.core.config import settings
from app.models.entities import Platform


def ensure_profile_dir(platform: Platform) -> Path:
    root = Path(settings.browser_profile_root).resolve()
    profile_dir = root / platform.value
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir
