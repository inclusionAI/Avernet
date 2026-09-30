"""Service API Protocol for the skill_center git-sync service.

Re-export only. The Protocol is defined in its owning core module
(``core/skill_center/git_sync_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.skill_center.git_sync_service_protocol import (
    GitSyncServiceProtocol,
)

__all__ = [
    "GitSyncServiceProtocol",
]
