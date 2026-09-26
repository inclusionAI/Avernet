"""Service API Protocol for OSS → NAS switch / rollback workflows.

Re-export only. The Protocol is defined in its owning core module
(``core/devices/oss_to_nas_switch_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.devices.oss_to_nas_switch_service_protocol import (
    OssToNasSwitchServiceProtocol,
)

__all__ = [
    "OssToNasSwitchServiceProtocol",
]
