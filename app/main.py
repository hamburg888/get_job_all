from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import settings
from app.core.logger import configure_logging
from app.db.database import Base, engine
from app.db.migrations import run_migrations
from app.services.bootstrap import bootstrap_demo_data

configure_logging()
Path(settings.preview_frame_root).mkdir(parents=True, exist_ok=True)
Path(settings.upload_root).mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(bind=engine)
run_migrations()
if settings.bootstrap_demo_data_enabled:
    bootstrap_demo_data()

app = FastAPI(
    title="Job Help API",
    version="0.1.0",
    description="Automation control plane for Zhilian, Qiancheng, and Boss job delivery tasks.",
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


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Job Help API is running."}
