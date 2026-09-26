"""OpenClaw adapter 测试桩:Noop(安全零值) + Mock(记录调用)。"""

from __future__ import annotations

import asyncio
import os
from typing import Any


class NoopOpenClawAdapter:
    """No-op OpenClaw adapter:返回安全零值、不做任何 I/O。

    Env vars for E2E failure-path tests:
    - ``BAAS_STUB_ENGINE_SESSION_ERROR=1`` — ``create_adapter_session()`` raises ``RuntimeError``
    - ``BAAS_STUB_ENGINE_SESSION_SLOW=1`` — adds a 2s delay to ``create_adapter_session()``
    """

    engine_type = "openclaw"

    def ws_path(self) -> str:
        return "/api/openclaw/ws"

    def session_consistency_key(
        self,
        *,
        tc_bot_id: str,
        user_id: str,
        run_id: str,
    ) -> str | None:
        return f"agent:main:session:{run_id}:user:{user_id}"

    async def create_adapter_session(
        self,
        *,
        session_client: Any,
        planned_id: str,
        user_id: str,
        metadata: dict[str, Any],
        bot_id: str,
        session_pending: bool = True,
    ) -> tuple[str, bool]:
        if os.getenv("BAAS_STUB_ENGINE_SESSION_ERROR"):
            raise RuntimeError("stub openclaw: simulated session creation failure")
        if os.getenv("BAAS_STUB_ENGINE_SESSION_SLOW"):
            await asyncio.sleep(2)
        return ("", True)


class MockOpenClawAdapter:
    """内存版 OpenClaw adapter:记录调用、返回可预期值,供单测断言。"""

    engine_type = "openclaw"

    def __init__(
        self,
        *,
        session_result: tuple[str, bool] = ("mock-openclaw-session", False),
    ) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self._session_result = session_result

    def ws_path(self) -> str:
        self.calls.append(("ws_path",))
        return "/api/openclaw/ws"

    def session_consistency_key(
        self,
        *,
        tc_bot_id: str,
        user_id: str,
        run_id: str,
    ) -> str | None:
        self.calls.append(("session_consistency_key", tc_bot_id, user_id, run_id))
        return f"agent:main:session:{run_id}:user:{user_id}"

    async def create_adapter_session(
        self,
        *,
        session_client: Any,
        planned_id: str,
        user_id: str,
        metadata: dict[str, Any],
        bot_id: str,
        session_pending: bool = True,
    ) -> tuple[str, bool]:
        self.calls.append(("create_adapter_session", bot_id, planned_id))
        return self._session_result
