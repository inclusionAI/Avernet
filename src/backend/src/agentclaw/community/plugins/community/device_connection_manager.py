"""Community ``DeviceConnectionManagerPlugin`` — no Moltis bot-to-bot gateway.

The Moltis device-connection gateway is a corp runtime; the community build has
none, so every method returns the safe "no device available" default. A real
(fail-open) impl bound by ``CommunityDevicesModule`` — not a ``MockSeam``.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.plugin_api.device_connection_manager import DeviceConnectionManagerPlugin


class CommunityDeviceConnectionManager(DeviceConnectionManagerPlugin):
    """No remote device gateway in the community build; returns None / no-op."""

    async def get_device_ip(
        self, entity_id: str, entity_type: str, bot_id: str
    ) -> str | None:
        return None

    async def get_connection(
        self, entity_id: str, entity_type: str, bot_id: str
    ) -> Any | None:
        return None

    async def get_client(
        self, entity_id: str, entity_type: str, bot_id: str
    ) -> Any | None:
        return None

    async def close_client(
        self, entity_id: str, entity_type: str, bot_id: str
    ) -> None:
        return None

    async def close_all(self) -> None:
        return None
