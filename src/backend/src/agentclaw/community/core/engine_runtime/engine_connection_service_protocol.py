"""Service API Protocol for composing a bot's public socket connections."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.engine_runtime.models import ConnectionResult


@runtime_checkable
class EngineConnectionServiceProtocol(Protocol):
    """Compose the sockets a caller may open against their bot."""

    def build(
        self, *, bot_id: str, owner_id: str, caller_id: str, stage: str
    ) -> ConnectionResult:
        """Return the addressed bot's usable sockets for one stage.

        ``owner_id`` names the bot's owner (the caller's own id for their own
        bot); ``caller_id`` is the verified caller, adjudicated as the bot's
        operator — owner or member-level collaborator — before any device
        work. ``stage`` names which runtime the socket addresses; required
        with no default, like the relay's, so the stage that was gated and
        the stage that is composed cannot silently diverge.
        """
        ...


__all__ = ["EngineConnectionServiceProtocol"]
