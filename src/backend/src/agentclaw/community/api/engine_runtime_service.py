"""Service API Protocol for the engine-runtime relay.

Re-export only. The Protocol is defined in its owning core module
(``core/engine_runtime/engine_runtime_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.engine_runtime.engine_runtime_service_protocol import (
    BotFacts,
    EngineResult,
    EngineRuntimeRelayProtocol,
)

__all__ = [
    "BotFacts",
    "EngineResult",
    "EngineRuntimeRelayProtocol",
]
