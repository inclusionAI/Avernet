"""AppScheduler — 统一调度管理器

持有单个 AsyncIOScheduler 实例，通过 ``add_task`` 注册实现 ``ScheduledTask``
协议的 task，不依赖具体 task 类。
"""

from __future__ import annotations

from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from secbaas.logger import get_logger

from ._protocols import ScheduledTask

log = get_logger("core-scheduler")


class AppScheduler:
    """统一调度管理器

    用法::

        scheduler = AppScheduler()
        scheduler.add_task(device_ttl_timer_task)
        scheduler.add_task(bot_run_recovery_task)
        scheduler.start()
        ...
        scheduler.stop()
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(
            timezone="Asia/Shanghai",
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )
        self._running = False

    def add_task(self, task: ScheduledTask) -> None:
        """注册一个定时 task

        Args:
            task: 实现 ``ScheduledTask`` 协议的 task 实例，
                需提供 ``name``、``interval_seconds`` 和 ``run`` 方法。
        """
        job_id = f"{task.name}_job"
        self._scheduler.add_job(
            func=task.run,
            trigger=IntervalTrigger(seconds=task.interval_seconds),
            id=job_id,
            name=task.name,
            next_run_time=datetime.now(),
        )
        log.info(
            "[AppScheduler] Registered %s, interval=%ds",
            task.name,
            task.interval_seconds,
        )

    def start(self) -> None:
        if self._running:
            log.warning("[AppScheduler] Already running")
            return
        self._running = True
        self._scheduler.start()
        log.info("[AppScheduler] Started")

    def stop(self) -> None:
        if not self._running:
            log.warning("[AppScheduler] Not running")
            return
        self._running = False
        self._scheduler.shutdown(wait=False)
        log.info("[AppScheduler] Stopped")
