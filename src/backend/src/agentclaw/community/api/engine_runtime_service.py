"""Service API Protocol for the engine-runtime relay.

Declares **real signatures**, not ``*args/**kwargs``, so
``tests/community/architecture/test_service_api_conformance.py`` can assert full
signature equality against ``EngineRuntimeRelay`` — parameter names, kinds,
defaults, and coroutine status. Keep the two in step: a single changed default
fails that gate.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.engine_runtime.models import BotFacts, EngineResult


@runtime_checkable
class EngineRuntimeRelayProtocol(Protocol):
    """Forward one public runtime request to a bot's engine adapter."""

    def resolve_bot(self, bot_id: str, owner_id: str) -> BotFacts:
        """Return the caller's bot facts, or raise ``BotNotFoundError``.

        Exposed because handlers must branch on bot facts *before* deciding
        whether to forward at all — the sessions group reads ``bot_type``, the
        connection endpoint reads ``active_engine``. Returns a narrow value
        object, not the raw record, so device binding internals cannot reach a
        public handler by accident.
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
        enveloped: bool = True,
    ) -> EngineResult:
        """Issue ``method path`` against the caller's bot's engine adapter.

        ``enveloped=False`` for the one engine route that answers with a raw
        payload instead of the standard envelope (``GET /api/engine/status``).
        """
        ...


__all__ = ["EngineRuntimeRelayProtocol"]
