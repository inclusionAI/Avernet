"""DeviceConnectionManagerPlugin -- manages connections to device Moltis gateways.

Abstracts obtaining a per-(entity, bot) gateway client / connection
info. The production implementation
(``plugins.prod.moltis_connection_manager.DeviceConnectionManager``)
talks to ``DeviceService`` in-process and pools Moltis connections.

Defined here (not on the concrete class) so ``api/`` routes can depend
on the binding key via ``Injected(DeviceConnectionManagerPlugin)``
without importing ``plugins``. Return types are kept as ``Any``
so this protocol module stays free of ``core``/``plugins``
imports.
"""

from typing import Any, Protocol
from agentclaw.community.plugin_api.base import Plugin


class DeviceConnectionManagerPlugin(Plugin, Protocol):
    """Resolve and pool connections to a device's Moltis gateway."""

    async def get_device_ip(
        self, entity_id: str, entity_type: str, bot_id: str
    ) -> str | None:
        """Return the device IP for the (entity, bot), or None."""
        ...

    async def get_connection(
        self, entity_id: str, entity_type: str, bot_id: str
    ) -> Any | None:
        """Return the device connection info, or None."""
        ...

    async def get_client(
        self, entity_id: str, entity_type: str, bot_id: str
    ) -> Any | None:
        """Return a (pooled) gateway client for the device, or None."""
        ...

    async def close_client(
        self, entity_id: str, entity_type: str, bot_id: str
    ) -> None:
        """Close and evict the pooled client for the (entity, bot)."""
        ...

    async def close_all(self) -> None:
        """Close every pooled client."""
        ...
