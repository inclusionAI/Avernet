"""Service API Protocol for OSS-to-NAS file migration.

Re-export only. The Protocol is defined in its owning core module
(``core/devices/oss_to_nas_migration_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.devices.oss_to_nas_migration_service_protocol import (
    OssToNasMigrationServiceProtocol,
)

__all__ = [
    "OssToNasMigrationServiceProtocol",
]
