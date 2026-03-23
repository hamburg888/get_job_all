from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import settings
from app.core.logger import configure_logging
from app.db.database import Base, engine
from app.db.migrations import run_migrations
from app.services.bootstrap import bootstrap_demo_data

configure_logging()
Base.metadata.create_all(bind=engine)
run_migrations()
bootstrap_demo_data()

app = FastAPI(
    title="Job Help API",
    version="0.1.0",
    description="Automation control plane for Zhilian and Boss job delivery tasks.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Job Help API is running."}
