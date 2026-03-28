import logging
from logging.config import dictConfig
from pathlib import Path

from app.core.config import settings


def configure_logging() -> None:
    log_root = Path(settings.log_root).resolve()
    log_root.mkdir(parents=True, exist_ok=True)

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                },
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "formatter": "default",
                    "filename": str((log_root / "job_help.log").resolve()),
                    "maxBytes": 2 * 1024 * 1024,
                    "backupCount": 3,
                    "encoding": "utf-8",
                },
            },
            "root": {"handlers": ["console", "file"], "level": settings.log_level},
        }
    )
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
