"""Service API Protocol for composing a bot's public socket connections.

Re-export only. The Protocol is defined in its owning core module
(``core/engine_runtime/engine_connection_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.engine_runtime.engine_connection_service_protocol import (
    ConnectionResult,
    EngineConnectionServiceProtocol,
)

__all__ = [
    "ConnectionResult",
    "EngineConnectionServiceProtocol",
]
