"""Cron lifecycle management — AppScheduler orchestration.

This private bootstrap module owns the cron subsystem's lifecycle:
- ``AppScheduler`` task registration and start/stop

每个 task 内部通过分布式锁保证多机/多 worker 下同一时刻只有一个执行，
因此不需要进程级文件锁做 leader 选举。
"""

from __future__ import annotations

from secbaas.community.core.service.scheduler import AppScheduler, ScheduledTask
from secbaas.community.logger import get_logger

log = get_logger("bootstrap-cron")


class CronLifecycle:
    """Container-managed cron lifecycle: AppScheduler orchestration.

    Attributes:
        _app_scheduler: The AppScheduler instance (provider-injected).
        _tasks: List of ScheduledTask instances (provider-injected).
    """

    def __init__(
        self,
        app_scheduler: AppScheduler,
        tasks: list[ScheduledTask],
    ) -> None:
        self._app_scheduler = app_scheduler
        self._tasks = tasks

    def _start_sync(self) -> None:
        """Register and start AppScheduler tasks.

        All exceptions are caught and logged — this method never raises.
        """
        try:
            log.info("Starting scheduled jobs")
            for task in self._tasks:
                self._app_scheduler.add_task(task)
            self._app_scheduler.start()
            log.info("AppScheduler started")
        except Exception:
            log.exception("Cron startup failed; continuing without scheduled jobs")

    def _stop_sync(self) -> None:
        """Stop the AppScheduler.

        All exceptions are caught and logged — this method never raises.
        """
        try:
            self._app_scheduler.stop()
        except Exception:
            log.exception("Error stopping AppScheduler")

        log.info("Cron lifecycle stopped")

    # -- Lifecycle Protocol --------------------------------------------------

    async def start(self) -> None:
        """Lifecycle.start: delegate to sync implementation."""
        self._start_sync()

    async def stop(self) -> None:
        """Lifecycle.stop: delegate to sync implementation."""
        self._stop_sync()
