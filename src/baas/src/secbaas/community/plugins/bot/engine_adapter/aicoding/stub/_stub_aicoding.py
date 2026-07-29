"""AICoding adapter 测试桩:Noop(安全零值) + Mock(记录调用)。"""

from __future__ import annotations

import asyncio
import os
from typing import Any


class NoopAICodingAdapter:
    """No-op AICoding adapter:返回安全零值、不做任何 I/O。

    Env vars for E2E failure-path tests:
    - ``BAAS_STUB_ENGINE_SESSION_ERROR=1`` — ``create_adapter_session()`` raises ``RuntimeError``
    - ``BAAS_STUB_ENGINE_SESSION_SLOW=1`` — adds a 2s delay to ``create_adapter_session()``
    """

    engine_type = "aicoding"

    def ws_path(self) -> str:
        return "/api/ws"

    def session_consistency_key(
        self,
        *,
        tc_bot_id: str,
        user_id: str,
        run_id: str | None,
        session_id: str | None = None,
    ) -> str | None:
        return None

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
        if os.getenv("BAAS_STUB_ENGINE_SESSION_ERROR"):
            raise RuntimeError("stub aicoding: simulated session creation failure")
        if os.getenv("BAAS_STUB_ENGINE_SESSION_SLOW"):
            await asyncio.sleep(2)
        return ("", True)


class MockAICodingAdapter:
    """内存版 AICoding adapter:记录调用、返回可预期值,供单测断言。"""

    engine_type = "aicoding"

    def __init__(
        self, *, session_result: tuple[str, bool] = ("mock-aicoding-session", False)
    ) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self._session_result = session_result

    def ws_path(self) -> str:
        self.calls.append(("ws_path",))
        return "/api/ws"

    def session_consistency_key(
        self,
        *,
        tc_bot_id: str,
        user_id: str,
        run_id: str | None,
        session_id: str | None = None,
    ) -> str | None:
        self.calls.append(
            ("session_consistency_key", tc_bot_id, user_id, run_id, session_id)
        )
        if session_id is not None:
            return session_id
        return None

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
        self.calls.append(("create_adapter_session", bot_id, session_id, run_id))
        return self._session_result
