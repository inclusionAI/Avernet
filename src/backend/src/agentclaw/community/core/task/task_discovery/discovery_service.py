"""DiscoveryService — 任务主动发现编排核心。

编排完整流程：
1. ``TaskReader`` 读取已发现的待确认任务 (mock 数据)
2. 为每个任务通过 ``SessionCreator`` 创建 engine session（获得 session_id）
3. session 创建成功后通过 ``NotifySenderPlugin`` 投递通知（任务详情，不含 session 链接）
4. 用户在前端确认后，由执行框架处理（不在本模块）

session_url 不在 discover 阶段构建 — 用户 bot 没有单独的 session_url。

使用方式::

    service = DiscoveryService(
        reader=SqliteTaskReader("scripts/.dependencies/data/discovered_tasks.db"),
        session_creator=EngineSessionCreator(),
        notify_sender=CommunityNotifySender(),
    )

    # discover — 读取任务 + 创建 session + 投递通知
    results = await service.discover(user_id="u001", agent_id="bot_001")
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    TaskReader,
    SqliteTaskReader,
)
from agentclaw.community.core.task.task_discovery.session_creator import (
    SessionCreator,
    HttpSessionCreator,
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

    将 TaskReader、SessionCreator 和 NotifySenderPlugin 编排在一起，
    提供 "发现 → 创建 session → 通知" 流程。

    session_url 不在 discover 阶段构建。任务确认后的执行由执行框架负责。
    """

    def __init__(
        self,
        reader: TaskReader,
        session_creator: SessionCreator,
        notify_sender: NotifySenderPlugin,
    ):
        self._reader = reader
        self._session_creator = session_creator
        self._notify_sender = notify_sender

        #: 最近的发现结果 (task_id → DiscoveryResult)，供外部查询
        self._discoveries: dict[str, DiscoveryResult] = {}

    async def discover(
        self,
        *,
        user_id: str,
        agent_id: str,
        bot_id: str,
        owner_id: str,
        model: str | None = None,
    ) -> list[DiscoveryResult]:
        """执行发现流程：读取任务 → 创建 session → 投递通知。

        Args:
            user_id: 用户 ID（通知接收者）。
            agent_id: Bot/Agent ID。
            bot_id: Bot ID（用于 relay 路由 per-bot engine）。
            owner_id: Bot 所有者 ID。
            model: 可选模型覆盖。

        Returns:
            每个待确认任务的 :class:`DiscoveryResult` 列表。
        """
        tasks = self._reader.read_pending_tasks()
        logger.info(
            "[task_discovery] discovered %d pending tasks", len(tasks)
        )

        results: list[DiscoveryResult] = []
        for task in tasks:
            result = await self._discover_single(
                task,
                user_id=user_id,
                agent_id=agent_id,
                bot_id=bot_id,
                owner_id=owner_id,
                model=model,
            )
            results.append(result)
            self._discoveries[task.task_id] = result

        return results

    async def _discover_single(
        self,
        task: DiscoveredTask,
        *,
        user_id: str,
        agent_id: str,
        bot_id: str,
        owner_id: str,
        model: str | None,
    ) -> DiscoveryResult:
        """处理单个任务的发现流程：创建 session → 投递通知。"""
        try:
            session = await self._session_creator.create_session(
                task,
                user_id=user_id,
                agent_id=agent_id,
                bot_id=bot_id,
                owner_id=owner_id,
                model=model,
            )
            message = task.to_notification_message()

            notification_sent = self._send_notification(task, user_id)

            logger.info(
                "[task_discovery] task %s → session %s (notified=%s)",
                task.task_id,
                session.session_id,
                notification_sent,
            )

            return DiscoveryResult(
                task=task,
                session=session,
                notification_message=message,
                notification_sent=notification_sent,
            )
        except Exception as exc:
            logger.error(
                "[task_discovery] failed to create session for task %s: %s",
                task.task_id,
                exc,
            )
            return DiscoveryResult(task=task, error=str(exc))

    def _send_notification(
        self,
        task: DiscoveredTask,
        user_id: str,
    ) -> bool:
        """通过 NotifySenderPlugin 投递通知，返回是否发送成功。

        NotifySenderPlugin Protocol 约定 send() 从不抛异常；
        返回 str 为消息 ID（成功），None 为失败。
        通知不含 session 链接 — session_url 不在 discover 阶段构建。
        """
        message = NotifyMessage(
            title="发现待确认任务",
            body=task.to_notification_message(),
            recipient=user_id,
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

    def print_notifications(self, results: list[DiscoveryResult]) -> None:
        """将发现结果的通知消息打印到 stdout（供 CLI 输出）。"""
        for result in results:
            if result.success:
                print(result.notification_message)
                print()
            elif result.error:
                print(f"❌ 任务 {result.task.task_id} 发现失败: {result.error}")
                print()


def create_default_service(
    data_file: str,
    notify_sender: NotifySenderPlugin,
    session_creator: SessionCreator,
) -> DiscoveryService:
    """使用默认实现创建 DiscoveryService。

    Args:
        data_file: SQLite db 文件路径(discovered_tasks 表)。
        notify_sender: 通知发送插件（session 创建成功后投递通知）。
        session_creator: SessionCreator 实例（用于在 per-bot engine 上创建 session）。

    Returns:
        配置好的 :class:`DiscoveryService`
    """
    return DiscoveryService(
        reader=SqliteTaskReader(data_file),
        session_creator=session_creator,
        notify_sender=notify_sender,
    )


__all__ = [
    "DiscoveryService",
    "DiscoveryResult",
    "create_default_service",
]
