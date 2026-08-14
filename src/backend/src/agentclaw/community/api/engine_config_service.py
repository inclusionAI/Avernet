"""Service API Protocol for the engine-config service."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.service_bot.repository.models import (
        BotPublishRecord,
    )


@runtime_checkable
class EngineConfigServiceProtocol(Protocol):
    """Service API for provider-blind reads/writes of a bot's engine config.

    HTTP adapters depend on this rather than the concrete
    ``core.services.engine_config.EngineConfigService``: the resolver/dispatcher
    wiring behind it is core's business, and an adapter that names the class can
    reach past the layer boundary for anything else on it.

    Signatures are spelled out rather than ``*args, **kwargs`` — unlike the
    older Protocols in this package — because a contract that accepts anything
    checks nothing. Renaming a parameter on the implementation would otherwise
    stay invisible to type checking and to protocol conformance while every
    request failed at runtime with ``TypeError``, and the adapters' mocks would
    not catch the drift either. The keyword-only markers mirror the
    implementation, so a positional call is a type error here too.
    """

    async def read_bot_config(
        self,
        *,
        bot_id: str,
        owner_id: str,
        entity_id: str,
        entity_type: str,
        engine_type: str,
    ) -> dict[str, Any]:
        """Read a bot's engine config from its own device, provider-blind."""
        ...

    async def write_bot_config(
        self,
        *,
        bot_id: str,
        owner_id: str,
        entity_id: str,
        entity_type: str,
        engine_type: str,
        config: dict[str, Any],
    ) -> None:
        """Write a bot's engine config to its own device, provider-blind."""
        ...

    async def read_publish_config(
        self, record: BotPublishRecord, engine_type: str
    ) -> dict[str, Any]:
        """Read a published bot's engine config from its active-stage device."""
        ...
