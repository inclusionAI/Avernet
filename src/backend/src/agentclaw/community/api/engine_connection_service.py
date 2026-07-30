"""Service API Protocol for composing a bot's public socket connections."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.engine_runtime.models import ConnectionResult


@runtime_checkable
class EngineConnectionServiceProtocol(Protocol):
    """Compose the sockets a caller may open against their bot."""

    def build(self, *, bot_id: str, owner_id: str) -> ConnectionResult:
        """Return the bot's usable sockets, owner-scoped."""
        ...


__all__ = ["EngineConnectionServiceProtocol"]
