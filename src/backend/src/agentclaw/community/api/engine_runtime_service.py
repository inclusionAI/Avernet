"""Service API Protocol for the engine-runtime relay.

Declares **real signatures**, not ``*args/**kwargs``, so
``tests/community/architecture/test_service_api_conformance.py`` can assert full
signature equality against ``EngineRuntimeRelay`` — parameter names, kinds,
defaults, and coroutine status. Keep the two in step: a single changed default
fails that gate.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.engine_runtime.models import EngineResult


@runtime_checkable
class EngineRuntimeRelayProtocol(Protocol):
    """Forward one public runtime request to a bot's engine adapter."""

    def resolve_bot(self, bot_id: str, owner_id: str) -> dict[str, Any]:
        """Return the caller's bot record, or raise ``BotNotFoundError``.

        Exposed on the Protocol because handlers need the record itself — the
        sessions group reads ``bot_type`` from it, and the connection endpoint
        reads ``active_engine`` — not because callers should re-check ownership.
        ``call`` already resolves the bot; this is for handlers that must branch
        on bot facts *before* deciding whether to forward at all.
        """
        ...

    async def call(
        self,
        *,
        bot_id: str,
        owner_id: str,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> EngineResult:
        """Issue ``method path`` against the caller's bot's engine adapter."""
        ...


__all__ = ["EngineRuntimeRelayProtocol"]
