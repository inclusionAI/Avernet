"""Service API Protocol for the engine-config service."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EngineConfigServiceProtocol(Protocol):
    """Service API for provider-blind reads/writes of a bot's engine config.

    HTTP adapters depend on this rather than the concrete
    ``core.services.engine_config.EngineConfigService``: the resolver/dispatcher
    wiring behind it is core's business, and an adapter that names the class can
    reach past the layer boundary for anything else on it.
    """

    async def read_bot_config(self, *args: Any, **kwargs: Any) -> Any: ...

    async def write_bot_config(self, *args: Any, **kwargs: Any) -> Any: ...

    async def read_publish_config(self, *args: Any, **kwargs: Any) -> Any: ...
