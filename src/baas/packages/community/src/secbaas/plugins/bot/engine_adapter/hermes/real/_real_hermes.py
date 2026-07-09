"""Hermes 引擎 adapter。

两处引擎特有行为:

1. ``session_consistency_key`` 返回 ``agent:{tc_bot_id}:session:{run_id}:user:{user_id}``
   作为 device 亲和键（与 claude_code 同形），让同一会话的请求稳定落到同一设备。

2. ``create_adapter_session``:Hermes 侧新建 session 后，其真正可用的 session_id 是异步
   持久化产生的（形如 ``YYYYMMDD_HHMMSS_xxxxxx`` 或 ``api-xxxxx``）。若直接用创建即时返回的
   临时 id 去发消息会失效，因此这里在创建后轮询等待持久化 id 就绪;超过
   ``session_persist_timeout_seconds`` 仍未就绪则抛 ``BotNotAvailableError``。
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from secbaas.api.bot_runtime import BotNotAvailableError
from secbaas.logger import get_logger

from ..._base import BaseEngineAdapter

logger = get_logger("core-bot-run")

# Hermes 持久化 session_id 形态:YYYYMMDD_HHMMSS_xxxxxx 或 api-xxxxx
_PERSISTENT_RE = re.compile(r"^\d{8}_\d{6}_\w{6,}$|^api-\w+$")

_DEFAULT_PERSIST_TIMEOUT_SECONDS = 5.0
_DEFAULT_POLL_INTERVAL_SECONDS = 0.2


def _is_persistent(session_id: str | None) -> bool:
    return bool(session_id) and bool(_PERSISTENT_RE.match(session_id))


class HermesAdapter(BaseEngineAdapter):
    """Hermes 引擎 adapter —— WS 路径 ``/api/hermes/ws``，创建后等待持久化 session_id。"""

    engine_type = "hermes"
    _WS_PATH = "/api/hermes/ws"

    def __init__(
        self,
        *,
        session_persist_timeout_seconds: float = _DEFAULT_PERSIST_TIMEOUT_SECONDS,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._persist_timeout = session_persist_timeout_seconds
        self._poll_interval = poll_interval_seconds

    def session_consistency_key(
        self,
        *,
        tc_bot_id: str,
        user_id: str,
        run_id: str | None,
        session_id: str | None = None,
    ) -> str | None:
        if session_id is not None:
            return session_id
        return f"agent:{tc_bot_id}:session:{run_id}:user:{user_id}"

    async def create_adapter_session(
        self,
        *,
        session_client: Any,
        session_id: str | None,
        user_id: str,
        metadata: dict[str, Any],
        bot_id: str,
        run_id: str | None,
    ) -> tuple[str, bool]:
        if session_id:
            logger.info(
                "Adapter session already exists: session_id=%s, reusing", session_id
            )
            return session_id, True

        adapter_session = await session_client.create_session(
            title=metadata.get("title", None),
            user_id=user_id,
            agent_id=bot_id,
            uuid=run_id,
            model=metadata.get("model", None),
            engine=self.engine_type,
        )
        candidate = adapter_session.id
        if _is_persistent(candidate):
            logger.info("Hermes session created (persistent): session_id=%s", candidate)
            return candidate, False

        persistent = await self._await_persistent_session(session_client, candidate)
        if persistent is None:
            raise BotNotAvailableError(bot_id, "hermes session persistence timeout")
        logger.info("Hermes session persisted: session_id=%s", persistent)
        return persistent, False

    async def _await_persistent_session(
        self, session_client: Any, candidate: str
    ) -> str | None:
        """轮询 get_session 直到拿到持久化 session_id,或超时返回 None。"""
        deadline = time.monotonic() + self._persist_timeout
        current = candidate
        while time.monotonic() < deadline:
            await asyncio.sleep(self._poll_interval)
            try:
                refreshed = await session_client.get_session(
                    current, engine=self.engine_type
                )
            except Exception as e:  # noqa: BLE001 — 轮询容错,继续重试直至超时
                logger.debug("hermes persistence poll failed: %s", e)
                continue
            current = getattr(refreshed, "id", current) or current
            if _is_persistent(current):
                return current
        return None
