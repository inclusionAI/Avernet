"""Service API Protocol for device-config (allocation lists, provider selection).

Re-export only. The Protocol is defined in its owning core module
(``core/system_config/device_config_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.system_config.device_config_service_protocol import (
    DeviceConfigServiceProtocol,
)

__all__ = [
    "DeviceConfigServiceProtocol",
]
