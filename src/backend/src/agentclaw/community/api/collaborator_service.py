"""Service API Protocol for bot collaborator management.

Re-export only. The Protocol is defined in its owning core module
(``core/bot_collaborator/collaborator_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_collaborator.collaborator_service_protocol import (
    CollaboratorRecord,
    CollaboratorRole,
    CollaboratorServiceProtocol,
    PermissionLevel,
)

__all__ = [
    "CollaboratorRecord",
    "CollaboratorRole",
    "CollaboratorServiceProtocol",
    "PermissionLevel",
]
