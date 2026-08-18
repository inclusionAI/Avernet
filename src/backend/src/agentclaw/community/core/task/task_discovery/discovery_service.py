"""DiscoveryService — 任务主动发现编排核心。

编排完整流程：
1. ``TaskReader`` 读取已发现的待确认任务 (mock 数据)
2. 为每个任务通过 ``SessionCreator`` 创建 engine session + session_url
3. 输出通知信息（任务详情 + session_url），供用户在前端确认

任务执行不在本模块负责 — 由 task 目录下另外的执行框架处理。

使用方式::

    service = DiscoveryService(
        reader=MockTaskReader("scripts/data/discovered_tasks.json"),
        session_creator=EngineSessionCreator(),
    )

    # discover — 读取任务 + 创建 session
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
    MockTaskReader,
)
from agentclaw.community.core.task.task_discovery.session_creator import (
    SessionCreator,
    EngineSessionCreator,
)
from agentclaw.community.log import get_logger

logger = get_logger()


@dataclass
class DiscoveryResult:
    """单次发现流程的结果。"""

    task: DiscoveredTask
    session: Optional[DiscoverySession] = None
    notification_message: str = ""
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.session is not None and self.error is None


class DiscoveryService:
    """任务主动发现编排服务。

    将 TaskReader 和 SessionCreator 编排在一起，
    提供完整的 "发现 → 通知" 流程。

    任务确认后的执行由 task 目录下另外的执行框架负责，
    不在本服务职责内。
    """

    def __init__(
        self,
        reader: TaskReader,
        session_creator: SessionCreator,
    ):
        self._reader = reader
        self._session_creator = session_creator

        #: 最近的发现结果 (task_id → DiscoveryResult)，供外部查询
        self._discoveries: dict[str, DiscoveryResult] = {}

    async def discover(
        self,
        *,
        user_id: str,
        agent_id: str,
        model: str | None = None,
    ) -> list[DiscoveryResult]:
        """执行发现流程：读取任务 → 创建 session → 生成通知。

        Args:
            user_id: 用户 ID。
            agent_id: Bot/Agent ID。
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
                task, user_id=user_id, agent_id=agent_id, model=model
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
        model: str | None,
    ) -> DiscoveryResult:
        """处理单个任务的发现流程。"""
        try:
            session = await self._session_creator.create_session(
                task,
                user_id=user_id,
                agent_id=agent_id,
                model=model,
            )
            message = task.to_notification_message(session.session_url)

            logger.info(
                "[task_discovery] task %s → session %s (url=%s)",
                task.task_id,
                session.session_id,
                session.session_url,
            )

            return DiscoveryResult(
                task=task,
                session=session,
                notification_message=message,
            )
        except Exception as exc:
            logger.error(
                "[task_discovery] failed to create session for task %s: %s",
                task.task_id,
                exc,
            )
            return DiscoveryResult(task=task, error=str(exc))

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
    engine_base_url: str | None = None,
    engine_frontend_url: str | None = None,
) -> DiscoveryService:
    """使用默认实现创建 DiscoveryService。

    Args:
        data_file: mock 任务数据文件路径。
        engine_base_url: Engine API 地址。
        engine_frontend_url: 前端 workbench 地址（构建 session_url）。

    Returns:
        配置好的 :class:`DiscoveryService`
    """
    return DiscoveryService(
        reader=MockTaskReader(data_file),
        session_creator=EngineSessionCreator(
            engine_base_url=engine_base_url,
            engine_frontend_url=engine_frontend_url,
        ),
    )


__all__ = [
    "DiscoveryService",
    "DiscoveryResult",
    "create_default_service",
]