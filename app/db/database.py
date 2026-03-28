import json
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings
from app.db.runtime_support import ensure_runtime_database


class Base(DeclarativeBase):
    pass


ensure_runtime_database(settings.database_url, settings.desktop_db_template_path)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    json_serializer=lambda value: json.dumps(value, ensure_ascii=False),
    json_deserializer=json.loads,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
