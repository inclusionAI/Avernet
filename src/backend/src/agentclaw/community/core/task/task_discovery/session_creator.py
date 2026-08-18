"""SessionCreator — 通过 EngineRuntimeRelay 为发现的任务创建 session。

调用 backend 内部的 ``EngineRuntimeRelayProtocol`` 将 ``POST /api/sessions``
转发到 bot 对应的 per-bot engine adapter，确保 session 落在正确的
OpenClaw Gateway 上（而非全局 standalone engine）。

创建后构建 ``session_url`` 供用户在浏览器中打开确认。

与 cron ``run-single`` 的区别：
  - ``run-single`` 直接创建 session 并开始执行
  - 本模块只创建 session（不触发执行），等用户确认后再由 executor 执行
"""
from __future__ import annotations

import os
from typing import Any, Protocol

from agentclaw.community.api.engine_runtime_service import (
    EngineRuntimeRelayProtocol,
)
from agentclaw.community.core.engine_runtime.models import BotFacts
from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: 默认前端 workbench 端口（singlebox local, frontend.sh:8000）
_DEFAULT_FRONTEND_PORT = "8000"

#: 个人 bot 的 stage（relay 对个人 bot 忽略 stage）
_DEFAULT_STAGE = "draft"


class SessionCreator(Protocol):
    """Engine session 创建接口。"""

    async def create_session(
        self,
        task: DiscoveredTask,
        *,
        user_id: str,
        agent_id: str,
        bot_id: str,
        owner_id: str,
        model: str | None = None,
    ) -> DiscoverySession:
        """为任务创建 engine session，返回 session_id 和 session_url。"""
        ...


class RelaySessionCreator:
    """通过 ``EngineRuntimeRelayProtocol`` 创建 session 的实现。

    利用 backend 已有的 relay 机制自动路由到 bot 对应的 per-bot engine
    adapter（如 singlebox 中 BaaS 动态分配的 20010-20099 端口），
    而不是硬编码全局 engine 地址。
    """

    def __init__(
        self,
        relay: EngineRuntimeRelayProtocol,
        *,
        frontend_url: str | None = None,
    ):
        """初始化。

        Args:
            relay: 后端 engine runtime relay 协议实例（由 DI 注入）。
            frontend_url: 前端 workbench 地址（用于构建 session_url）。
                若为 ``None`` 则从环境变量 ``FRONTEND_URL`` 读取，
                默认 ``http://localhost:8000``。
        """
        self._relay = relay
        self._frontend_url = frontend_url or os.environ.get(
            "FRONTEND_URL",
            f"http://localhost:{_DEFAULT_FRONTEND_PORT}",
        )

    async def create_session(
        self,
        task: DiscoveredTask,
        *,
        user_id: str,
        agent_id: str,
        bot_id: str,
        owner_id: str,
        model: str | None = None,
    ) -> DiscoverySession:
        """为任务创建 engine session。

        Args:
            task: 已发现的待确认任务。
            user_id: 用户 ID（调用者 + 通知接收者）。
            agent_id: Bot/Agent ID。
            bot_id: Bot ID（用于 relay 路由定位 per-bot engine）。
            owner_id: Bot 所有者 ID。
            model: 可选模型覆盖。

        Returns:
            包含 session_id 和 session_url 的 :class:`DiscoverySession`

        Raises:
            Exception: relay 调用失败或 session 创建未返回有效 id 时抛出。
        """
        body: dict[str, Any] = {
            "title": task.project_name,
            "user_id": user_id,
            "agent_id": agent_id,
            "extInfo": task.to_session_ext_info(),
        }
        if model:
            body["model"] = model

        logger.info(
            "[task_discovery] creating session via relay for task %s "
            "bot=%s owner=%s",
            task.task_id,
            bot_id,
            owner_id,
        )

        facts: BotFacts = await self._relay.resolve_bot_off_loop(
            bot_id, owner_id, caller_id=user_id,
        )

        result = await self._relay.call(
            bot_id=bot_id,
            owner_id=owner_id,
            facts=facts,
            stage=_DEFAULT_STAGE,
            method="POST",
            path="/api/sessions",
            body=body,
        )

        session_data = result.data if isinstance(result.data, dict) else {}
        session_id = session_data.get("id") or session_data.get("session_id", "")
        if not session_id:
            raise RuntimeError(
                f"engine session creation returned no session id: {result.data}"
            )

        session_url = self._build_session_url(session_id, agent_id)
        logger.info(
            "[task_discovery] session created: id=%s url=%s",
            session_id,
            session_url,
        )

        return DiscoverySession(
            task_id=task.task_id,
            session_id=session_id,
            session_url=session_url,
        )

    def _build_session_url(self, session_id: str, agent_id: str) -> str:
        """构建用户可访问的前端 workbench session URL。

        前端 SessionOnlyPage 路由期望三个 query 参数:
        - ``bot_uuid``: bot 标识
        - ``id``: 群组 ID(task_discovery 无 BCS 群,用 agent_id 作为容器标识)
        - ``session``: engine session ID

        格式: ``{frontend_url}/bcn/chat/session?bot_uuid={agent_id}&id={agent_id}&session={session_id}``

        用户点击后会跳到该 bot 的对话界面，看到任务发现的通知消息。
        """
        base = self._frontend_url.rstrip("/")
        return (
            f"{base}/bcn/chat/session"
            f"?bot_uuid={agent_id}&id={agent_id}&session={session_id}"
        )


__all__ = ["SessionCreator", "RelaySessionCreator"]