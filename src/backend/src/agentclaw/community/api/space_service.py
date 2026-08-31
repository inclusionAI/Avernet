"""Service API contracts for spaces and members.

Re-export only. The Protocol is defined in its owning core module
(``core/spaces/space_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.spaces.space_service_protocol import (
    SpaceAccessServiceProtocol,
    SpaceMemberServiceProtocol,
    SpaceServiceProtocol,
)

__all__ = [
    "SpaceAccessServiceProtocol",
    "SpaceMemberServiceProtocol",
    "SpaceServiceProtocol",
]
