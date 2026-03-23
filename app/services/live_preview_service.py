import asyncio
import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.core.time_utils import local_now
from app.models.entities import Platform


@dataclass
class PreviewState:
    platform: str
    task_run_id: int | None = None
    active: bool = False
    enabled: bool = False
    headless_enabled: bool = False
    interval_ms: int = 1000
    seq: int = 0
    frame_url: str | None = None
    updated_at: datetime | None = None
    current_step: str = "暂无任务"
    message: str = "当前暂无实时画面。"


class LivePreviewService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, PreviewState] = {}
        self._frame_root = Path(settings.preview_frame_root).resolve()
        self._frame_root.mkdir(parents=True, exist_ok=True)

    def start_session(
        self,
        platform: Platform | str,
        task_run_id: int | None,
        *,
        headless_enabled: bool,
        live_preview_enabled: bool,
        interval_ms: int,
        current_step: str,
    ) -> None:
        platform_key = platform.value if isinstance(platform, Platform) else platform
        platform_dir = self._platform_dir(platform_key)
        for frame_path in platform_dir.glob("frame_*.jpg"):
            frame_path.unlink(missing_ok=True)

        if not headless_enabled:
            message = "当前任务未开启无头模式，实时观看不可用。"
        elif not live_preview_enabled:
            message = "当前任务未开启实时观看。"
        else:
            message = "实时预览准备中，等待浏览器画面。"

        with self._lock:
            self._states[platform_key] = PreviewState(
                platform=platform_key,
                task_run_id=task_run_id,
                active=headless_enabled and live_preview_enabled,
                enabled=live_preview_enabled,
                headless_enabled=headless_enabled,
                interval_ms=max(500, interval_ms),
                current_step=current_step,
                message=message,
            )

    def update_status(
        self,
        platform: Platform | str,
        *,
        task_run_id: int | None = None,
        current_step: str | None = None,
        active: bool | None = None,
        message: str | None = None,
    ) -> None:
        platform_key = platform.value if isinstance(platform, Platform) else platform
        with self._lock:
            state = self._states.setdefault(platform_key, PreviewState(platform=platform_key))
            if task_run_id is not None:
                state.task_run_id = task_run_id
            if current_step is not None:
                state.current_step = current_step
            if active is not None:
                state.active = active
            if message is not None:
                state.message = message
            state.updated_at = local_now()

    def finish_session(
        self,
        platform: Platform | str,
        *,
        task_run_id: int | None = None,
        current_step: str | None = None,
        message: str | None = None,
    ) -> None:
        platform_key = platform.value if isinstance(platform, Platform) else platform
        with self._lock:
            state = self._states.setdefault(platform_key, PreviewState(platform=platform_key))
            if task_run_id is not None:
                state.task_run_id = task_run_id
            if current_step is not None:
                state.current_step = current_step
            state.active = False
            state.updated_at = local_now()
            if message is not None:
                state.message = message
            elif state.frame_url:
                state.message = "任务已结束，保留最后一帧预览。"
            else:
                state.message = "任务已结束，未生成实时画面。"

    async def capture_page(self, platform: Platform | str, task_run_id: int, page) -> None:
        platform_key = platform.value if isinstance(platform, Platform) else platform
        with self._lock:
            state = self._states.get(platform_key)
            if state is None or state.task_run_id != task_run_id or not state.enabled or not state.headless_enabled:
                return
            next_seq = state.seq + 1
            slot = next_seq % 5
            frame_path = self._platform_dir(platform_key) / f"frame_{slot}.jpg"

        await page.screenshot(path=str(frame_path), type="jpeg", quality=65)

        with self._lock:
            state = self._states.get(platform_key)
            if state is None or state.task_run_id != task_run_id:
                return
            state.seq = next_seq
            state.frame_url = f"/preview_frames/{platform_key}/frame_{slot}.jpg?v={next_seq}"
            state.updated_at = local_now()
            state.active = True
            state.message = "实时预览更新中。"

    def snapshot(self, platform: Platform | str) -> dict:
        platform_key = platform.value if isinstance(platform, Platform) else platform
        with self._lock:
            state = self._states.get(platform_key) or PreviewState(platform=platform_key)
            return asdict(state)

    async def stream(self, platform: Platform | str):
        platform_key = platform.value if isinstance(platform, Platform) else platform
        last_payload = ""
        while True:
            payload = json.dumps(self.snapshot(platform_key), ensure_ascii=False, default=self._json_default)
            if payload != last_payload:
                last_payload = payload
                yield f"event: preview\ndata: {payload}\n\n"
            await asyncio.sleep(0.8)

    def _platform_dir(self, platform_key: str) -> Path:
        platform_dir = self._frame_root / platform_key
        platform_dir.mkdir(parents=True, exist_ok=True)
        return platform_dir

    @staticmethod
    def _json_default(value):
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"Unsupported preview payload type: {type(value)!r}")


live_preview_service = LivePreviewService()
