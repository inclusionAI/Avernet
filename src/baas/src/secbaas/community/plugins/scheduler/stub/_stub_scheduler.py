from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from secbaas.community.spi.scheduler import SchedulerPlugin


class StubSchedulerPlugin(SchedulerPlugin):
    def __init__(
        self,
        job_func: Callable[..., Any] | None = None,
    ) -> None:
        self._job_func = job_func
        self._running = False
        self._task: asyncio.Task[Any] | None = None

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    def trigger_now(self) -> None:
        if self._job_func is not None:
            loop = asyncio.get_event_loop()
            self._task = (
                loop.create_task(self._job_func())
                if asyncio.iscoroutinefunction(self._job_func)
                else None
            )
            if self._task is None and callable(self._job_func):
                self._job_func()
