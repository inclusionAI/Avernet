"""SessionCreator — 通过 backend connection API 定位 per-bot engine 后直连创建 session。

singlebox 模式下 ``DeviceAdapterTransport`` 绑的是 ``InMemoryDeviceAdapterTransport``
(mock)，relay 的 ``call()`` 不会做真实 HTTP 转发。因此 session_creator 需要自己：
  1. 调 backend ``GET /api/bots/{bot_id}/connection`` 拿到 per-bot engine 的 target
  2. 直连 ``http://{target}/api/sessions`` 创建 session

这样 session 会落在 bot 对应的 per-bot OpenClaw Gateway 上，前端可见。

创建后构建 ``session_url`` 供用户在浏览器中打开确认。
"""
from __future__ import annotations

import os
from typing import Any, Protocol

import httpx

from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: 默认 backend 地址（singlebox local）
_DEFAULT_BACKEND_URL = "http://localhost:8888"

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
        bot_id: str,
        owner_id: str,
        model: str | None = None,
    ) -> DiscoverySession:
        """为任务创建 engine session，返回 session_id 和 session_url。"""
        ...


class HttpSessionCreator:
    """通过 backend connection API 定位 per-bot engine 后直连创建 session。

    1. ``GET {backend_url}/api/bots/{bot_id}/connection`` → 拿到 ``target`` (如 ``127.0.0.1:20010``)
    2. ``POST http://{target}/api/sessions`` → 在 per-bot engine 上创建 session
    """

    def __init__(
        self,
        *,
        backend_url: str = "http://localhost:8888",
        frontend_url: str = "http://localhost:8000",
    ):
        """初始化。

        Args:
            backend_url: Backend API 地址（用于查 bot connection）。
            frontend_url: 前端 workbench 地址（用于构建 session_url）。
                运行时可通过 FrontendUrlHolder (API 注入) 覆盖。
        """
        self._backend_url = backend_url
        self._frontend_url = frontend_url

    async def _resolve_engine_target(
        self, bot_id: str, owner_id: str, user_id: str,
    ) -> str:
        """通过 backend API 查 per-bot engine 的 target 地址。

        个人 bot 没有 publish record，``/api/bots/{bot_id}/connection`` 会 404，
        所以先查 bot detail 拿 ``binding_id``，再用
        ``/api/v1/devices/{binding_id}/connection`` 查 target。

        Returns:
            如 ``localhost:20010``
        """
        async with httpx.AsyncClient(timeout=30.0) as cli:
            # Step 1: 查 bot detail 拿 binding_id
            bot_resp = await cli.get(
                f"{self._backend_url}/api/bots/{bot_id}",
                params={"owner_id": owner_id},
                headers={"x-user-id": user_id},
            )
            bot_resp.raise_for_status()
            bot_data = (bot_resp.json().get("data") or {})
            binding_id = bot_data.get("binding_id")
            if not binding_id:
                raise RuntimeError(
                    f"bot {bot_id} has no binding_id (owner={owner_id})"
                )

            # Step 2: 用 binding_id 查 device connection
            conn_resp = await cli.get(
                f"{self._backend_url}/api/v1/devices/{binding_id}/connection",
                headers={"x-user-id": user_id},
            )
            conn_resp.raise_for_status()
            data = (conn_resp.json().get("data") or {})
            target = data.get("target") or ""
            if not target:
                raise RuntimeError(
                    f"backend connection API returned no target for bot={bot_id}"
                )
            logger.info(
                "[task_discovery] resolved engine target for bot=%s → %s",
                bot_id, target,
            )
            return target

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
            bot_id: Bot ID（用于查 per-bot engine 地址）。
            owner_id: Bot 所有者 ID。
            model: 可选模型覆盖。

        Returns:
            包含 session_id 和 session_url 的 :class:`DiscoverySession`

        Raises:
            httpx.HTTPError: 请求失败时抛出。
        """
        target = await self._resolve_engine_target(bot_id, owner_id, user_id)

        body: dict[str, Any] = {
            "title": task.title,
            "user_id": user_id,
            "agent_id": agent_id,
            "extInfo": task.to_session_ext_info(),
        }
        if model:
            body["model"] = model

        url = f"http://{target}/api/sessions"
        logger.info(
            "[task_discovery] creating session for task %s → %s",
            task.task_id, url,
        )

        async with httpx.AsyncClient(timeout=30.0) as cli:
            resp = await cli.post(
                url, json=body, headers={"x-user-id": user_id},
            )
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
            session_id, session_url,
        )

        return DiscoverySession(
            task_id=task.task_id,
            session_id=session_id,
            session_url=session_url,
        )

    def _build_session_url(self, session_id: str, agent_id: str) -> str:
        """构建用户可访问的前端 workbench session URL。

        格式: ``{frontend_url}/assistant?botId={bot_id}&sessionId={session_key}``
        其中 session_key 为 ``agent:main:{raw_session_id}`` URL-encoded。

        动态解析 frontend URL — 支持运行时 API 注入（FrontendUrlHolder）。
        """
        from urllib.parse import quote

        from agentclaw.community.core.task.task_discovery.session_initiator import (
            FrontendUrlHolder,
        )
        base = (FrontendUrlHolder.get() or self._frontend_url).rstrip("/")
        full_session_key = f"agent:main:{session_id}"
        encoded_sid = quote(full_session_key, safe="")
        return f"{base}/assistant?botId={agent_id}&sessionId={encoded_sid}"


__all__ = ["SessionCreator", "HttpSessionCreator"]