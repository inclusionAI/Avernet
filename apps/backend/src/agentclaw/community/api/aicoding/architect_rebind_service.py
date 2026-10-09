"""Service API Protocol for the aicoding architect-bot rebind feature.

Re-export only. The Protocol is defined in its owning core module
(``core/aicoding/architect_rebind_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.aicoding.architect_rebind_service_protocol import (
    ArchitectRebindServiceProtocol,
)

__all__ = [
    "ArchitectRebindServiceProtocol",
]
