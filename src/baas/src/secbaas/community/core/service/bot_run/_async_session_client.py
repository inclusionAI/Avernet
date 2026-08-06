"""Session HTTP client for BotService.

Provides async HTTP client for session management operations,
following the same API design as adapter session_router.py endpoints.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any, cast

import aiohttp

from secbaas.community.core.utils.env_utils import is_dev
from secbaas.community.logger import get_logger

logger = get_logger("core-bot-run")


@dataclass
class SessionInfo:
    """会话信息"""

    id: str
    title: str
    user_id: str | None = None
    agent_id: str | None = None
    model: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    message_count: int = 0
    last_message: dict[str, Any] | None = None


@dataclass
class MessageInfo:
    """消息信息"""

    id: str
    session_id: str
    role: str
    content: str
    meta: dict[str, Any] | None = None
    created_at: str | None = None
    history_meta: dict[str, Any] | None = None


class AsyncSessionClient:
    """异步会话管理客户端

    封装会话管理的 HTTP API 调用，支持：
    - 创建会话
    - 获取会话列表
    - 获取会话详情
    - 更新会话
    - 删除会话
    - 获取会话消息
    - 清空会话消息

    使用示例:
        async with SessionClient(base_url, headers=headers) as client:
            session = await client.create_session(title="My Session", user_id="user123")
            sessions = await client.list_sessions(user_id="user123")
            messages = await client.get_messages(session.id)
    """

    def __init__(
        self,
        base_url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        engine: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.timeout = timeout
        self.engine = engine
        self._session: aiohttp.ClientSession | None = None

    async def list_sessions(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
        engine: str | None = None,
    ) -> list[SessionInfo]:
        params = {
            "user_id": user_id,
            "agent_id": agent_id,
            "limit": limit,
            "offset": offset,
            "engine": engine or self.engine,
        }

        # IMPORTANT: no need to pass 'agent_id' for openclaw engine
        if (engine or self.engine) == 'openclaw':
            params["agent_id"] = None

        resp = await self._request("GET", "/api/sessions", params=params)
        data = resp.get("data", [])
        return [self._parse_session_info(item) for item in data]

    async def create_session(
        self,
        title: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
        uuid: str | None = None,
        model: str | None = None,
        engine: str | None = None,
        session_id: str | None = None,
    ) -> SessionInfo:
        body = {
            "title": title,
            "user_id": user_id,
            "agent_id": agent_id,
            "model": model,
            "engine": engine or self.engine,
        }
        if uuid:
            body["uuid"] = uuid
        # FIXME Special case only for teclaw
        if session_id:
            body["uuid"] = session_id
        body = {k: v for k, v in body.items() if v is not None}

        resp = await self._request("POST", "/api/sessions", json=body)
        data = resp.get("data")
        if not data:
            raise RuntimeError("No session data returned")
        return self._parse_session_info(data)

    async def get_session(
        self,
        session_id: str,
        engine: str | None = None,
    ) -> SessionInfo:
        params = {"engine": engine or self.engine}
        encoded_session_id = base64.b64encode(session_id.encode()).decode()
        resp = await self._request(
            "GET", f"/api/sessions/{encoded_session_id}", params=params
        )
        data = resp.get("data")
        if not data:
            raise RuntimeError("No session data returned")
        return self._parse_session_info(data)

    async def update_session(
        self,
        session_id: str,
        title: str | None = None,
        model: str | None = None,
        engine: str | None = None,
    ) -> SessionInfo:
        params = {
            "title": title,
            "model": model,
            "engine": engine or self.engine,
        }
        params = {k: v for k, v in params.items() if v is not None}

        resp = await self._request(
            "POST", f"/api/sessions/{session_id}/update", params=params
        )
        data = resp.get("data")
        if not data:
            raise RuntimeError("No session data returned")
        return self._parse_session_info(data)

    async def delete_session(
        self,
        session_id: str,
        force: bool = False,
        engine: str | None = None,
    ) -> bool:
        params = {
            "force": force,
            "engine": engine or self.engine,
        }
        params = {k: v for k, v in params.items() if v is not None}

        await self._request("DELETE", f"/api/sessions/{session_id}", params=params)
        return True

    async def get_messages(
        self,
        session_id: str,
        limit: int | None = None,
        offset: int = 0,
        engine: str | None = None,
    ) -> list[MessageInfo]:
        params = {
            "limit": limit,
            "offset": offset,
            "engine": engine or self.engine,
        }
        params = {k: v for k, v in params.items() if v is not None}
        encoded_session_id = base64.b64encode(session_id.encode()).decode()
        resp = await self._request(
            "GET", f"/api/sessions/{encoded_session_id}/messages", params=params
        )
        data = resp.get("data", [])
        return [self._parse_message_info(item) for item in data]

    async def clear_messages(
        self,
        session_id: str,
        engine: str | None = None,
    ) -> bool:
        params = {"engine": engine or self.engine}
        params = {k: v for k, v in params.items() if v is not None}

        await self._request(
            "DELETE", f"/api/sessions/{session_id}/messages", params=params
        )
        return True

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> AsyncSessionClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> bool:
        await self.close()
        return False

    # ── 私有方法 ──────────────────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            )
        return self._session

    def _build_url(self, path: str, params: dict[str, Any] | None = None) -> str:
        url = f"{self.base_url}{path}"
        if params:
            # fmt: off
            query = "&".join(
                f"{k}={v}" for k, v in params.items() if v is not None
            )
            # fmt: on
            if query:
                url = f"{url}?{query}"
        return url

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = await self._get_session()
        url = self._build_url(path, params)
        logger.debug(f"[SessionClient] {method} {url}")

        # dev 环境下设置额外 header
        headers: dict[str, str] | None = None
        if is_dev():
            iam_token = os.getenv("IAM_TOKEN") or ""
            headers = {
                "Cookie": f"iam_token={iam_token}",
            }

        try:
            async with session.request(method, url, json=json, headers=headers) as resp:
                body = await resp.json()
        except (TimeoutError, aiohttp.ClientError) as e:
            logger.error(
                "[SessionClient] Request failed: method=%s url=%s error=%s",
                method,
                url,
                e,
            )
            raise RuntimeError(f"SessionClient request failed: {e}") from e

        if resp.status >= 400:
            error_msg = body.get("detail", str(body))
            raise aiohttp.ClientResponseError(
                request_info=resp.request_info,
                history=resp.history,
                status=resp.status,
                message=error_msg,
            )

        if not body.get("success"):
            raise RuntimeError(f"API error: {body.get('message', 'Unknown error')}")

        return cast(dict[str, Any], body)

    def _parse_session_info(self, data: dict[str, Any]) -> SessionInfo:
        return SessionInfo(
            id=data.get("id", ""),
            title=data.get("title", ""),
            user_id=data.get("user_id"),
            agent_id=data.get("agent_id"),
            model=data.get("model"),
            created_at=data.get("gmt_created"),
            updated_at=data.get("gmt_modified"),
            message_count=data.get("message_count", 0),
            last_message=data.get("last_message"),
        )

    def _parse_message_info(self, data: dict[str, Any]) -> MessageInfo:
        return MessageInfo(
            id=data.get("id", ""),
            session_id=data.get("session_id", ""),
            role=data.get("role", ""),
            content=data.get("content", ""),
            meta=data.get("metadata"),
            created_at=data.get("gmt_created"),
            history_meta=data.get("history_meta"),
        )
