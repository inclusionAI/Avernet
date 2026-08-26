"""TaskDiscoveryScheduler — 后端定时任务发现调度器。

使用 APScheduler BackgroundScheduler 实现线程级 cron 调度。
在独立线程中运行 tick 循环，不占用 asyncio 事件循环。
定时触发时通过 ``asyncio.run()`` 调用 ``DiscoveryService.discover_all_bots()``。

参考 ``TaskDiscoveryLifecycle`` 的模式（已替代）：
- 继承 ``LifecycleBase``
- 在 ``startup()`` 中调度 cron 任务
- 在 ``shutdown()`` 中停止调度
- 通过 DI 容器自动发现，走 ``discover_lifecycle_participants`` 机制

配置项:
  TASK_DISCOVERY_AUTO_START   是否启用自动调度 (true/false, 默认 true)
  TASK_DISCOVERY_CRON         cron 表达式 (默认 "0 11 * * *")
  TASK_DISCOVERY_TIMEZONE     调度时区 (默认 "Asia/Shanghai")
"""
from __future__ import annotations

import asyncio
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from injector import inject

from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryService,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

logger = get_logger()

#: 默认 cron 表达式 — 每日 11:00
_DEFAULT_CRON = "0 11 * * *"
_DEFAULT_TIMEZONE = "Asia/Shanghai"


class TaskDiscoveryScheduler(LifecycleBase):
    """后端定时任务发现调度器 — 线程级 cron 调度，非 asyncio。

    使用 APScheduler BackgroundScheduler：
    - 在独立线程中运行 tick 循环，不占用 asyncio 事件循环
    - 支持标准 cron 表达式 + 时区
    - DreamMode 开启 → 确保调度器运行
    - DreamMode 关闭 → 停止调度
    """

    @inject
    def __init__(
        self,
        discovery_service: DiscoveryService,
    ) -> None:
        self._service: DiscoveryService = discovery_service
        self._scheduler: BackgroundScheduler | None = None

    async def startup(self) -> None:
        """Lifecycle hook — 启动 cron 定时调度。"""
        if os.environ.get("TASK_DISCOVERY_AUTO_START", "true").lower() != "true":
            logger.info(
                "[task_discovery] auto-schedule disabled "
                "(TASK_DISCOVERY_AUTO_START != true)"
            )
            return

        cron_expr = os.environ.get("TASK_DISCOVERY_CRON", _DEFAULT_CRON)
        tz = os.environ.get("TASK_DISCOVERY_TIMEZONE", _DEFAULT_TIMEZONE)

        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self._run_discovery,
            CronTrigger.from_crontab(cron_expr, timezone=tz),
            id="task_discovery_daily",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info(
            "[task_discovery] scheduler started — cron='%s' tz='%s'",
            cron_expr, tz,
        )

    async def shutdown(self) -> None:
        """Lifecycle hook — 停止定时调度。"""
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("[task_discovery] scheduler stopped")

    def _run_discovery(self) -> None:
        """在 scheduler 线程中执行；通过 asyncio.run 调用 async discover。

        scheduler 线程没有运行中的事件循环，所以用 ``asyncio.run()``
        创建临时事件循环执行 ``discover_all_bots()``。
        """
        try:
            asyncio.run(self._service.discover_all_bots())
        except Exception as exc:
            logger.error(
                "[task_discovery] scheduled discovery failed: %s",
                exc, exc_info=True,
            )

    def enable_for_bot(self, bot_id: str, owner_id: str) -> None:
        """DreamMode 开启 — 确保调度器运行。

        当前实现：全局调度器 — 开启任一 bot 的 DreamMode 即运行。
        未来可扩展为 per-bot 调度。
        """
        if self._scheduler is None:
            cron_expr = os.environ.get("TASK_DISCOVERY_CRON", _DEFAULT_CRON)
            tz = os.environ.get("TASK_DISCOVERY_TIMEZONE", _DEFAULT_TIMEZONE)
            self._scheduler = BackgroundScheduler()
            self._scheduler.add_job(
                self._run_discovery,
                CronTrigger.from_crontab(cron_expr, timezone=tz),
                id="task_discovery_daily",
                replace_existing=True,
            )
            self._scheduler.start()
            logger.info(
                "[task_discovery] scheduler enabled for bot=%s owner=%s",
                bot_id, owner_id,
            )
        else:
            logger.info(
                "[task_discovery] scheduler already running (enable_for_bot bot=%s)",
                bot_id,
            )

    def disable_for_bot(self, bot_id: str, owner_id: str) -> None:
        """DreamMode 关闭 — 停止调度。

        当前实现：全局停止。
        未来可扩展为 per-bot 调度（仅当所有 bot 都关闭时才停止）。
        """
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info(
                "[task_discovery] scheduler disabled for bot=%s owner=%s",
                bot_id, owner_id,
            )

    def reschedule(self, cron_expr: str, *, timezone: str | None = None) -> bool:
        """运行时修改 cron 触发时间 — 无需重启 backend。

        使用 APScheduler ``reschedule_job()`` 原地替换 job 的 trigger，
        新 cron 立即生效，旧的下一次执行计划被丢弃。

        Returns:
            True 如果 reschedule 成功；False 如果调度器未运行。
        """
        if self._scheduler is None or not self._scheduler.running:
            logger.warning("[task_discovery] reschedule called but scheduler not running")
            return False

        tz = timezone or os.environ.get("TASK_DISCOVERY_TIMEZONE", _DEFAULT_TIMEZONE)
        trigger = CronTrigger.from_crontab(cron_expr, timezone=tz)
        self._scheduler.reschedule_job("task_discovery_daily", trigger=trigger)
        os.environ["TASK_DISCOVERY_CRON"] = cron_expr
        logger.info("[task_discovery] reschedule done — cron='%s' tz='%s'", cron_expr, tz)
        return True

    def get_status(self) -> dict:
        """返回 APScheduler 当前调度状态 — 供 HTTP 端点查询。

        Returns:
            running: 调度器是否在运行
            jobs: 已注册的 job 列表（id, cron 表达式, next_run_time, 时区）
            auto_start: TASK_DISCOVERY_AUTO_START 配置值
            cron: TASK_DISCOVERY_CRON 配置值
            timezone: TASK_DISCOVERY_TIMEZONE 配置值
        """
        cron_expr = os.environ.get("TASK_DISCOVERY_CRON", _DEFAULT_CRON)
        tz = os.environ.get("TASK_DISCOVERY_TIMEZONE", _DEFAULT_TIMEZONE)
        auto_start = os.environ.get("TASK_DISCOVERY_AUTO_START", "true")

        if self._scheduler is None:
            return {
                "running": False,
                "jobs": [],
                "auto_start": auto_start,
                "cron": cron_expr,
                "timezone": tz,
            }

        jobs = []
        for job in self._scheduler.get_jobs():
            trigger = job.trigger
            jobs.append({
                "id": job.id,
                "cron": str(trigger),
                "next_run_time": (
                    job.next_run_time.isoformat()
                    if job.next_run_time else None
                ),
                "timezone": str(trigger.timezone) if hasattr(trigger, "timezone") else None,
            })

        return {
            "running": self._scheduler.running,
            "jobs": jobs,
            "auto_start": auto_start,
            "cron": cron_expr,
            "timezone": tz,
        }


__all__ = ["TaskDiscoveryScheduler"]