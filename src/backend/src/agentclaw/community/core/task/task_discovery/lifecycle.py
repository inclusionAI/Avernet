"""TaskDiscoveryLifecycle — backend 启动后定时为用户 bot 执行任务发现。

参考 ``CronAutoSetupListener`` 的模式：
- 继承 ``LifecycleBase``
- 在 ``startup()`` 中调度每日定时任务
- 在 ``shutdown()`` 中取消定时任务

默认每天 11:00 自动触发一次任务发现。
发现流程:
1. 从 BotService 查出当前所有用户 bot
2. 为每个 bot 读取已发现的任务数据 (mock)
3. 为每个待确认任务创建 engine session + 投递通知

手动触发可通过 HTTP API 或 CLI。

配置项:
  TASK_DISCOVERY_AUTO_START       是否启用自动调度 (true/false, 默认 true)
  TASK_DISCOVERY_SCHEDULE_HOUR    调度小时 (默认 11)
  TASK_DISCOVERY_SCHEDULE_MINUTE  调度分钟 (默认 0)
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path

from injector import inject

from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.task.task_discovery.discovery_service import (
    create_default_service,
)
from agentclaw.community.core.task.task_discovery.session_creator import (
    HttpSessionCreator,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.notify_sender import NotifySenderPlugin

logger = get_logger()

#: 默认 mock 数据文件路径 (相对于项目根目录)
_DEFAULT_DATA_FILE = "scripts/.dependencies/data/discovered_tasks.db"

#: 默认调度时间
_DEFAULT_SCHEDULE_HOUR = 11
_DEFAULT_SCHEDULE_MINUTE = 0


class TaskDiscoveryLifecycle(LifecycleBase):
    """backend 生命周期参与者 — 每天定时为用户 bot 执行任务发现。

    通过 DI 容器自动发现，与 ``CronAutoSetupListener`` 走相同的
    ``discover_lifecycle_participants`` 机制。

    默认每天 11:00 执行一次。手动触发可通过:
    - HTTP:  POST /api/public/task-discovery/discover
    - CLI:   ./scripts/task_discovery.sh discover
    """

    @inject
    def __init__(
        self,
        bot_service: BotServiceProtocol,
        notify_sender: NotifySenderPlugin,
    ) -> None:
        self._bot_service: BotServiceProtocol = bot_service
        self._notify_sender = notify_sender
        self._task: asyncio.Task | None = None

    async def startup(self) -> None:
        """Lifecycle hook — 调度每日定时任务发现。"""
        if os.environ.get("TASK_DISCOVERY_AUTO_START", "true").lower() != "true":
            logger.info(
                "[task_discovery] auto-schedule disabled "
                "(TASK_DISCOVERY_AUTO_START != true)"
            )
            return

        hour = int(os.environ.get("TASK_DISCOVERY_SCHEDULE_HOUR", _DEFAULT_SCHEDULE_HOUR))
        minute = int(os.environ.get("TASK_DISCOVERY_SCHEDULE_MINUTE", _DEFAULT_SCHEDULE_MINUTE))

        self._task = asyncio.create_task(self._run_daily_schedule(hour, minute))
        logger.info(
            "[task_discovery] daily schedule started — will trigger at %02d:%02d every day",
            hour,
            minute,
        )

    async def shutdown(self) -> None:
        """Lifecycle hook — 取消定时调度。"""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("[task_discovery] daily schedule stopped")

    async def _run_daily_schedule(self, hour: int, minute: int) -> None:
        """每日定时调度 — 计算到下一个目标时间点，sleep 后执行，循环。"""
        while True:
            delay = self._seconds_until(hour, minute)
            logger.info(
                "[task_discovery] next discovery at %02d:%02d (in %.0f seconds)",
                hour,
                minute,
                delay,
            )
            await asyncio.sleep(delay)
            await self._discover_once()

    def _seconds_until(self, hour: int, minute: int) -> float:
        """计算从现在到下一个目标时间的秒数。"""
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        delta = target - now
        return delta.total_seconds()

    async def _discover_once(self) -> None:
        """执行一次任务发现 — 遍历所有用户的 bot。"""
        bots = self._list_all_bots()
        if not bots:
            logger.info("[task_discovery] no bots found, skipping discovery")
            return

        logger.info(
            "[task_discovery] scheduled discovery triggered for %d bot(s)...",
            len(bots),
        )

        data_file = self._resolve_data_file()

        session_creator = HttpSessionCreator()

        total_discovered = 0
        for bot in bots:
            bot_id = bot.get("bot_id", "")
            owner_id = bot.get("owner_id", "")
            if not bot_id or not owner_id:
                continue

            try:
                service = create_default_service(
                    data_file=data_file,
                    notify_sender=self._notify_sender,
                    session_creator=session_creator,
                )
                results = await service.discover(
                    user_id=owner_id,
                    agent_id=bot_id,
                    bot_id=bot_id,
                    owner_id=owner_id,
                )
                succeeded = sum(1 for r in results if r.success)
                total_discovered += succeeded

                for r in results:
                    if r.success:
                        logger.info(
                            "[task_discovery]   ✓ bot=%s task=%s → session=%s notified=%s",
                            bot_id,
                            r.task.task_id,
                            r.session.session_id,
                            r.notification_sent,
                        )
                    else:
                        logger.warning(
                            "[task_discovery]   ✗ bot=%s task=%s: %s",
                            bot_id,
                            r.task.task_id,
                            r.error,
                        )
            except Exception as exc:
                logger.error(
                    "[task_discovery]   ✗ bot=%s failed: %s",
                    bot_id,
                    exc,
                    exc_info=True,
                )

        logger.info(
            "[task_discovery] discovery complete: %d task(s) discovered across %d bot(s)",
            total_discovered,
            len(bots),
        )

    def _list_all_bots(self) -> list[dict]:
        """从 BotService 查出所有用户的 bot（每个 bot 有 owner_id 和 bot_id）。"""
        try:
            result = self._bot_service.list_bots(page=1, page_size=100)
            return result.get("items", [])
        except Exception as exc:
            logger.error("[task_discovery] failed to list bots: %s", exc)
            return []

    def _resolve_data_file(self) -> str:
        """解析 mock 数据文件路径。"""
        project_root = Path(__file__).resolve()
        for _ in range(9):
            project_root = project_root.parent
        return os.environ.get(
            "TASK_DISCOVERY_DATA_FILE",
            str(project_root / _DEFAULT_DATA_FILE),
        )


__all__ = ["TaskDiscoveryLifecycle"]