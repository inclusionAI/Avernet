"""SessionCreator — 调用 engine API 为发现的任务创建 session。

调用 engine 的 ``POST /api/sessions`` 创建一个待确认的 session，
session 标题为任务项目名称，``extInfo`` 携带完整任务详情。
创建后构建 ``session_url`` 供用户在浏览器中打开确认。

与 cron ``run-single`` 的区别：
  - ``run-single`` 直接创建 session 并开始执行
  - 本模块只创建 session（不触发执行），等用户确认后再由 executor 执行
"""
from __future__ import annotations

from typing import Protocol

import httpx

from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: Engine session API 路径
_SESSIONS_PATH = "/api/sessions"

#: 默认 engine 端口（singlebox local, engine.sh:20003）
_DEFAULT_ENGINE_PORT = "20003"

#: 默认前端 workbench 端口（singlebox local, frontend.sh:8000）
_DEFAULT_FRONTEND_PORT = "8000"


class SessionCreator(Protocol):
    """Engine session 创建接口。"""

    async def create_session(
        self,
        task: DiscoveredTask,
        *,
        user_id: str,
        agent_id: str,
        model: str | None = None,
    ) -> DiscoverySession:
        """为任务创建 engine session，返回 session_id 和 session_url。"""
        ...


class EngineSessionCreator:
    """通过 engine HTTP API 创建 session 的实现。

    调用 ``POST {engine_base_url}/api/sessions`` 创建 session，
    然后用 engine 前端路由构建 ``session_url``。
    """

    def __init__(
        self,
        engine_base_url: str | None = None,
        engine_frontend_url: str | None = None,
    ):
        """初始化。

        Args:
            engine_base_url: Engine API 地址（如 ``http://localhost:20003``）。
                若为 ``None`` 则从环境变量 ``ENGINE_BASE_URL`` 读取，
                默认 ``http://localhost:20003``。
            engine_frontend_url: 前端 workbench 地址（用于构建 session_url）。
                若为 ``None`` 则从环境变量 ``FRONTEND_URL`` 读取，
                默认 ``http://localhost:8000``。
        """
        import os

        self._engine_base_url = engine_base_url or os.environ.get(
            "ENGINE_BASE_URL",
            f"http://localhost:{_DEFAULT_ENGINE_PORT}",
        )
        self._engine_frontend_url = engine_frontend_url or os.environ.get(
            "FRONTEND_URL",
            f"http://localhost:{_DEFAULT_FRONTEND_PORT}",
        )

    async def create_session(
        self,
        task: DiscoveredTask,
        *,
        user_id: str,
        agent_id: str,
        model: str | None = None,
    ) -> DiscoverySession:
        """为任务创建 engine session。

        Args:
            task: 已发现的待确认任务。
            user_id: 用户 ID。
            agent_id: Bot/Agent ID。
            model: 可选模型覆盖。

        Returns:
            包含 session_id 和 session_url 的 :class:`DiscoverySession`

        Raises:
            httpx.HTTPError: engine API 请求失败时抛出。
        """
        body: dict = {
            "title": task.project_name,
            "user_id": user_id,
            "agent_id": agent_id,
            "extInfo": task.to_session_ext_info(),
        }
        if model:
            body["model"] = model

        url = f"{self._engine_base_url}{_SESSIONS_PATH}"
        logger.info(
            "[task_discovery] creating engine session for task %s → %s",
            task.task_id,
            url,
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()

        data = resp.json()
        if not data.get("success", False):
            raise RuntimeError(
                f"engine session creation failed: {data.get('message', data)}"
            )

        session_data = data.get("data", {})
        session_id = session_data.get("id") or session_data.get("session_id", "")
        if not session_id:
            raise RuntimeError(f"engine response missing session id: {data}")

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
        base = self._engine_frontend_url.rstrip("/")
        return (
            f"{base}/bcn/chat/session"
            f"?bot_uuid={agent_id}&id={agent_id}&session={session_id}"
        )


__all__ = ["SessionCreator", "EngineSessionCreator"]
