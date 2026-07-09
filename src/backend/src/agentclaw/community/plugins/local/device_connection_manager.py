"""Local Noop ``DeviceConnectionManagerPlugin``.

Local mode has no remote device gateways to manage. Every method returns
the safe "no device available" default so callers can run without the
prod Moltis stack.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.plugin_api.device_connection_manager import DeviceConnectionManagerPlugin
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.NOOP,
    rationale="local mode has no remote device gateway; returns None / no-op",
)
class NoopDeviceConnectionManagerPlugin(MockSeam, DeviceConnectionManagerPlugin):
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
