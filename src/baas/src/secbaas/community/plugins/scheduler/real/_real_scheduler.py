from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from secbaas.community.spi.scheduler import SchedulerPlugin


class ApsSchedulerPlugin(SchedulerPlugin):
    def __init__(
        self,
        job_func: Callable[..., Any],
        interval_seconds: int = 3600,
    ) -> None:
        if job_func is None:
            raise ValueError("job_func is required")
        self._job_func = job_func
        self._interval_seconds = interval_seconds
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self._scheduler.add_job(
            self._job_func,
            "interval",
            seconds=self._interval_seconds,
            id="aps_scheduler_job",
            replace_existing=True,
        )
        self._scheduler.start()

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)

    def trigger_now(self) -> None:
        self._job_func()

    def close(self) -> None:
        self.stop()
