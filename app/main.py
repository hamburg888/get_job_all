from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import settings
from app.core.logger import configure_logging
from app.db.database import Base, engine
from app.db.migrations import run_migrations
from app.models import entities as _entities  # noqa: F401
from app.services.bootstrap import bootstrap_demo_data

configure_logging()
Path(settings.preview_frame_root).mkdir(parents=True, exist_ok=True)
Path(settings.upload_root).mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(bind=engine)
run_migrations()
if settings.bootstrap_demo_data_enabled:
    bootstrap_demo_data()

frontend_dist_path = Path(settings.frontend_dist_dir).resolve()

app = FastAPI(
    title="Job Help API",
    version="0.1.0",
    description="Automation control plane for Zhilian, Qiancheng, Liepin, and Boss job delivery tasks.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.mount("/preview_frames", StaticFiles(directory=settings.preview_frame_root), name="preview_frames")
app.mount("/uploads", StaticFiles(directory=settings.upload_root), name="uploads")


def _frontend_index_file() -> Path | None:
    index_file = frontend_dist_path / "index.html"
    return index_file if index_file.is_file() else None


def _resolve_frontend_asset(request_path: str) -> Path | None:
    if not frontend_dist_path.is_dir():
        return None

    normalized = request_path.strip().lstrip("/").replace("\\", "/")
    if not normalized:
        return _frontend_index_file()

    candidate = (frontend_dist_path / normalized).resolve()
    try:
        candidate.relative_to(frontend_dist_path)
    except ValueError:
        return None

    return candidate if candidate.is_file() else None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root():
    index_file = _frontend_index_file()
    if index_file:
        return FileResponse(index_file)
    return {"message": "Job Help API is running."}


@app.get("/{full_path:path}", include_in_schema=False)
def frontend_app(full_path: str):
    reserved_prefixes = ("api", "preview_frames", "uploads", "docs", "redoc", "openapi.json")
    if any(full_path == prefix or full_path.startswith(f"{prefix}/") for prefix in reserved_prefixes):
        raise HTTPException(status_code=404, detail="Not Found")

    asset_file = _resolve_frontend_asset(full_path)
    if asset_file:
        return FileResponse(asset_file)

    index_file = _frontend_index_file()
    if index_file:
        return FileResponse(index_file)

    raise HTTPException(status_code=404, detail="Not Found")
