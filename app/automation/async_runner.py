from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Awaitable
from queue import Queue
from typing import TypeVar

T = TypeVar("T")


def run_async_blocking(awaitable: Awaitable[T]) -> T:
    """Run Playwright coroutines safely from FastAPI worker threads on Windows."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _run_in_new_loop(awaitable)
    return _run_in_worker_thread(awaitable)


def _run_in_worker_thread(awaitable: Awaitable[T]) -> T:
    result_queue: Queue[tuple[bool, object]] = Queue(maxsize=1)

    def runner() -> None:
        try:
            result_queue.put((True, _run_in_new_loop(awaitable)))
        except Exception as exc:  # noqa: BLE001
            result_queue.put((False, exc))

    thread = threading.Thread(target=runner, name="job-help-async-runner", daemon=True)
    thread.start()
    success, payload = result_queue.get()
    thread.join()
    if success:
        return payload  # type: ignore[return-value]
    raise payload  # type: ignore[misc]


def _run_in_new_loop(awaitable: Awaitable[T]) -> T:
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(awaitable)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001
                pass
            asyncio.set_event_loop(None)
            loop.close()
    return asyncio.run(awaitable)
