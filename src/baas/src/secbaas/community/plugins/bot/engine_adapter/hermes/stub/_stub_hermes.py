"""Hermes adapter 测试桩:Noop(安全零值) + Mock(记录调用)。"""

from __future__ import annotations

from typing import Any


class NoopHermesAdapter:
    """No-op Hermes adapter:返回安全零值、不做任何 I/O。"""

    engine_type = "hermes"

    def ws_path(self) -> str:
        return "/api/hermes/ws"

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
        return ("", True)


class MockHermesAdapter:
    """内存版 Hermes adapter:记录调用、返回可预期值,供单测断言。"""

    engine_type = "hermes"

    def __init__(
        self,
        *,
        session_result: tuple[str, bool] = ("20260701_120000_abcdef", False),
    ) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self._session_result = session_result

    def ws_path(self) -> str:
        self.calls.append(("ws_path",))
        return "/api/hermes/ws"

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
        self.calls.append(("create_adapter_session", bot_id, session_id, run_id))
        return self._session_result
