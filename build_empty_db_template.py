import os
import sys
from pathlib import Path


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def main() -> int:
    output_path = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else (Path(__file__).resolve().parent / "desktop_assets" / "job_help.template.db").resolve()
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    os.environ["DATABASE_URL"] = _sqlite_url(output_path)
    os.environ["BOOTSTRAP_DEMO_DATA_ENABLED"] = "false"

    from app.db.database import Base, engine
    from app.db.migrations import run_migrations
    from app.models import entities as _entities  # noqa: F401

    Base.metadata.create_all(bind=engine)
    run_migrations()
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
