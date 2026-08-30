"""Service API Protocol for bot collaborator locking.

Re-export only. The Protocol is defined in its owning core module
(``core/bot_collaborator/collaborator_lock_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_collaborator.collaborator_lock_service_protocol import (
    CollaboratorLockServiceProtocol,
)

__all__ = [
    "CollaboratorLockServiceProtocol",
]
