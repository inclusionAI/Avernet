"""Service API Protocol for device allocation/lifecycle/inspection.

Re-export only. The Protocol is defined in its owning core module
(``core/devices/device_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.devices.device_service_protocol import (
    DeviceServiceProtocol,
)

__all__ = [
    "DeviceServiceProtocol",
]
