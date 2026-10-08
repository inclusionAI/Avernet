"""Service API Protocol for the engine-config service.

Re-export only. The Protocol is defined in its owning core module
(``core/services/engine_config_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.services.engine_config_service_protocol import (
    EngineConfigServiceProtocol,
)

__all__ = [
    "EngineConfigServiceProtocol",
]
