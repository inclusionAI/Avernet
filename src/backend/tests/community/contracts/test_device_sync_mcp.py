"""Contract propagation for Bot-scoped MCP delivery options."""

from __future__ import annotations

import inspect

from agentclaw.community.core.devices.services.baas_device_sync import (
    BaasDeviceSyncService,
)
from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.core.devices.services.singlebox_device_sync import (
    SingleboxDeviceSyncService,
)
from agentclaw.community.core.devices.services.teclaw_device_sync import (
    TeclawDeviceSyncService,
)


def test_mcp_delivery_contract_exposes_bot_url_and_strict_transport() -> None:
    required = {"url_override", "strict_transport_protocol"}

    for implementation in (
        DeviceSync,
        BaasDeviceSyncService,
        SingleboxDeviceSyncService,
        TeclawDeviceSyncService,
    ):
        parameters = inspect.signature(implementation.sync_single_mcp).parameters
        assert required <= parameters.keys(), implementation.__name__

