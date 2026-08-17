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

    ``stage`` is **required, with no default**, matching
    ``EngineRuntimeRelayProtocol``: "the stage a handler gated on and the stage
    it forwards to cannot silently diverge." A default would also have to be the
    same object on both sides — ``test_service_api_conformance.py`` compares
    defaults by value — which means this package importing
    ``core.engine_runtime`` at runtime, and that package's import graph reaches
    the DI container and a partially-initialised ``bot_service``. Convention and
    mechanics agree.
    """

    async def read_bot_config(
        self,
        *,
        bot_id: str,
        owner_id: str,
        entity_id: str,
        entity_type: str,
        engine_type: str,
        stage: str,
    ) -> dict[str, Any]:
        """Read a bot's engine config from the runtime ``stage`` names.

        ``draft`` is the bot's own workspace — what every caller read before
        stages were addressable.
        """
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
        stage: str,
    ) -> None:
        """Write a bot's engine config to its own device, provider-blind.

        Only the draft accepts a write; naming a published stage is refused
        rather than applied to the draft.
        """
        ...

    async def read_publish_config(
        self, record: BotPublishRecord, engine_type: str
    ) -> dict[str, Any]:
        """Read a published bot's engine config from its active-stage device."""
        ...
