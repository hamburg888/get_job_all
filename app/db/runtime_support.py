import shutil
import re
from pathlib import Path
from urllib.parse import unquote, urlparse


def sqlite_path_from_url(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:///"):
        return None

    parsed = urlparse(database_url)
    raw_path = unquote(parsed.path or "")
    if parsed.netloc and raw_path:
        normalized = f"{parsed.netloc}{raw_path}"
    else:
        normalized = raw_path

    if re.match(r"^/[A-Za-z]:/", normalized):
        normalized = normalized[1:]
    if not normalized:
        return None
    return Path(normalized)


def ensure_runtime_database(database_url: str, template_path: str) -> Path | None:
    db_path = sqlite_path_from_url(database_url)
    if db_path is None:
        return None

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        return db_path

    source_template = Path(template_path)
    if source_template.is_file() and source_template.resolve() != db_path.resolve():
        shutil.copy2(source_template, db_path)
    else:
        db_path.touch()
    return db_path
