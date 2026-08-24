"""DiscoveryService — 任务主动发现编排核心。

编排完整流程：
1. ``TaskReader`` 读取已发现的待确认任务 (按 bot_id/owner_id/dt 过滤)
2. 为每个 bot 的所有任务通过 ``SessionInitiator`` 创建 engine session（获得 session_id）
   — 同时通过 WebSocket ``chat.send`` 注入发现提示消息
3. session 创建成功后通过 ``NotifySenderPlugin`` 投递通知（发现摘要 + session 链接）
4. 用户在前端确认后，由执行框架处理（不在本模块）

使用方式::

    service = DiscoveryService(
        reader=SqliteTaskReader("scripts/.dependencies/data/discovered_tasks.db"),
        session_initiator=CronRelaySessionInitiator(cron_relay),
        notify_sender=CommunityNotifySender(),
    )

    # discover — 为单个 bot 读取任务 + 创建 session + 注入消息 + 投递通知
    results = await service.discover(bot_id="bot-001", owner_id="u001", agent_id="bot-001")

    # discover_all_bots — 遍历所有 bot（由 scheduler 线程调用）
    results = await service.discover_all_bots()
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.core.task.task_discovery.protocols import (
    BotServiceProtocol,
)
from agentclaw.community.core.task.task_discovery.session_initiator import (
    SessionInitiator,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    TaskReader,
    SqliteTaskReader,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.notify_sender import (
    NotifyMessage,
    NotifySenderPlugin,
)

logger = get_logger()


@dataclass
class DiscoveryResult:
    """单次发现流程的结果。"""

    task: DiscoveredTask
    session: Optional[DiscoverySession] = None
    notification_message: str = ""
    notification_sent: bool = False
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.session is not None and self.error is None


class DiscoveryService:
    """任务主动发现编排服务。

    将 TaskReader、SessionInitiator 和 NotifySenderPlugin 编排在一起，
    提供 "发现 → 创建 session+注入消息 → 通知" 流程。
    """

    def __init__(
        self,
        reader: TaskReader,
        session_initiator: SessionInitiator,
        notify_sender: NotifySenderPlugin,
        bot_service: BotServiceProtocol | None = None,
    ):
        self._reader = reader
        self._session_initiator = session_initiator
        self._notify_sender = notify_sender
        self._bot_service = bot_service

        #: 最近的发现结果 (task_id → DiscoveryResult)，供外部查询
        self._discoveries: dict[str, DiscoveryResult] = {}

    async def discover_all_bots(self) -> list[DiscoveryResult]:
        """遍历 db 中有待确认任务的 bot，为每个 bot 执行发现流程。

        由 scheduler 线程调用（通过 asyncio.run）。

        bot 列表 = ``discovered_tasks.db`` pending 任务提取的 bot ∩ ``list_bots()``
        返回的存活 bot —— db 里没数据不触发，bot 已删除也不瞎跑。

        TODO: 未来在两个集合交集的基础上，通过 dream mode 接口进一步缩小范围
        —— 只对开启了 dream mode 且任务发现 ready 的 bot 执行发现。
        """
        # 1) 从 db 读取所有 pending 任务，提取唯一 (bot_id, owner_id)
        pending = self._reader.read_pending_tasks()
        if not pending:
            logger.info("[task_discovery] no pending tasks in db, skipping discovery")
            return []

        db_bots: dict[str, tuple[str, str]] = {}  # bot_id → (bot_id, owner_id)
        for task in pending:
            if task.bot_id and task.owner_id and task.bot_id not in db_bots:
                db_bots[task.bot_id] = (task.bot_id, task.owner_id)

        # 2) 从 BotService 获取存活 bot 列表
        if self._bot_service is None:
            logger.warning("[task_discovery] no bot_service, cannot discover_all_bots")
            return []

        try:
            result = self._bot_service.list_bots(page=1, page_size=100)
        except Exception as exc:
            logger.error("[task_discovery] failed to list bots: %s", exc)
            return []

        live_bots = result.get("items", []) if isinstance(result, dict) else []
        live_bot_ids = {b.get("bot_id", "") for b in live_bots}

        # 3) 取交集：db 有 pending 任务 且 bot 存活
        # TODO: 交集基础上通过 dream mode 接口进一步过滤
        intersection = [
            db_bots[bid] for bid in db_bots if bid in live_bot_ids
        ]

        # 4) 按 owner_id 聚合 — 同一个 owner 只取第一个 bot 执行发现，
        #    避免同一用户多个 bot 重复发现
        seen_owners: set[str] = set()
        bots_to_discover: list[tuple[str, str]] = []
        for bot_id, owner_id in intersection:
            if owner_id not in seen_owners:
                seen_owners.add(owner_id)
                bots_to_discover.append((bot_id, owner_id))

        logger.info(
            "[task_discovery] scheduled discovery: db=%d bots, live=%d bots, "
            "intersection=%d, after owner aggregation=%d bot(s) (from %d pending tasks)...",
            len(db_bots), len(live_bots), len(intersection),
            len(bots_to_discover), len(pending),
        )

        all_results: list[DiscoveryResult] = []
        for bot_id, owner_id in bots_to_discover:
            try:
                results = await self.discover(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    agent_id=bot_id,
                )
                all_results.extend(results)
            except Exception as exc:
                logger.error(
                    "[task_discovery] bot=%s failed: %s",
                    bot_id, exc, exc_info=True,
                )

        logger.info(
            "[task_discovery] discovery complete: %d task(s) discovered across %d bot(s)",
            sum(1 for r in all_results if r.success),
            len(bots_to_discover),
        )
        return all_results

    async def discover(
        self,
        *,
        bot_id: str,
        owner_id: str,
        agent_id: str,
        model: str | None = None,
    ) -> list[DiscoveryResult]:
        """为单个 bot 执行发现流程（手动触发或遍历调用）。

        1. 读取该 bot 当天的待确认任务
        2. 为所有任务创建一个 engine session（extInfo 携带所有任务数据）
           — 同时通过 WebSocket 注入发现提示消息
        3. 发送通知（发现摘要 + session 链接）
        """
        dt = datetime.now().strftime("%Y-%m-%d")
        tasks = self._reader.read_pending_tasks_for_bot(bot_id, owner_id, dt)
        if not tasks:
            logger.info(
                "[task_discovery] no pending tasks for bot=%s owner=%s dt=%s",
                bot_id, owner_id, dt,
            )
            return []

        logger.info(
            "[task_discovery] discovered %d pending tasks for bot=%s",
            len(tasks), bot_id,
        )

        results: list[DiscoveryResult] = []
        for task in tasks:
            result = await self._discover_single(
                task,
                all_tasks=tasks,
                bot_id=bot_id,
                owner_id=owner_id,
                agent_id=agent_id,
                model=model,
            )
            results.append(result)
            self._discoveries[task.task_id] = result

        return results

    async def _discover_single(
        self,
        task: DiscoveredTask,
        *,
        all_tasks: list[DiscoveredTask],
        bot_id: str,
        owner_id: str,
        agent_id: str,
        model: str | None,
    ) -> DiscoveryResult:
        """处理单个任务：创建 session+注入消息 → 发通知。"""
        try:
            session = await self._session_initiator.initiate_session(
                all_tasks,
                bot_id=bot_id,
                owner_id=owner_id,
                agent_id=agent_id,
                model=model,
            )

            notification_sent = self._send_notification(
                task, owner_id, session.session_url, len(all_tasks),
            )

            logger.info(
                "[task_discovery] task %s → session %s (notified=%s)",
                task.task_id,
                session.session_id,
                notification_sent,
            )

            return DiscoveryResult(
                task=task,
                session=session,
                notification_sent=notification_sent,
            )
        except Exception as exc:
            logger.error(
                "[task_discovery] failed for task %s: %s",
                task.task_id, exc,
            )
            return DiscoveryResult(task=task, error=str(exc))

    def _send_notification(
        self,
        task: DiscoveredTask,
        user_id: str,
        session_url: str,
        task_count: int,
    ) -> bool:
        """通过 NotifySenderPlugin 投递通知，返回是否发送成功。

        NotifySenderPlugin Protocol 约定 send() 从不抛异常；
        返回 str 为消息 ID（成功），None 为失败。
        通知 body 是 bot 的「告知」：发现摘要 + 确认引导。
        deep_link 指向 session，用户点击后进入 session 确认。
        extra 携带通用交互卡片参数（不绑定具体服务商）。
        """
        message = NotifyMessage(
            title="发现待确认任务",
            body=task.to_notification_body(task_count),
            recipient=user_id,
            deep_link=session_url,
            extra={
                "channel": "tc_card",
                "card_template_id": os.environ.get(
                    "TASK_DISCOVERY_CARD_TEMPLATE_ID", ""
                ),
                "card_biz_id": f"discover_things_{task.task_id}",
                "card_data": json.dumps(task.to_card_data()),
                "session_url": session_url,
            },
        )
        msg_id = self._notify_sender.send(message)
        if msg_id:
            logger.info(
                "[task_discovery] notification sent for task %s (msg_id=%s)",
                task.task_id,
                msg_id,
            )
            return True
        else:
            logger.warning(
                "[task_discovery] notification send returned None for task %s",
                task.task_id,
            )
            return False

    def get_discovery_result(self, task_id: str) -> DiscoveryResult | None:
        """返回某个 task 的最近发现结果（含 session_id/session_url），供 status 接口查询。

        从内存 ``self._discoveries`` 读取 — 后端重启后会丢，仅反映进程内最近的 discover 结果。
        """
        return self._discoveries.get(task_id)


class _SessionCreatorAdapter:
    """Adapt the single-task HTTP creator to the discovery service protocol."""

    def __init__(self, creator) -> None:
        self._creator = creator

    async def initiate_session(
        self,
        tasks: list[DiscoveredTask],
        *,
        bot_id: str,
        owner_id: str,
        agent_id: str,
        model: str | None = None,
    ) -> DiscoverySession:
        if not tasks:
            raise ValueError("at least one task is required")
        return await self._creator.create_session(
            tasks[0],
            user_id=owner_id,
            agent_id=agent_id,
            bot_id=bot_id,
            owner_id=owner_id,
            model=model,
        )


def create_default_service(*, data_file: str, notify_sender, session_creator) -> DiscoveryService:
    """Build the lifecycle's default service from its infrastructure seams."""
    return DiscoveryService(
        reader=SqliteTaskReader(data_file),
        session_initiator=_SessionCreatorAdapter(session_creator),
        notify_sender=notify_sender,
    )


__all__ = [
    "DiscoveryService",
    "DiscoveryResult",
    "create_default_service",
]