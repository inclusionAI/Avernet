"""Service API Protocol for skill_center sync.

Re-export only. The Protocol is defined in its owning core module
(``core/skill_center/skill_center_sync_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.skill_center.skill_center_sync_service_protocol import (
    SkillCenterSyncServiceProtocol,
)

__all__ = [
    "SkillCenterSyncServiceProtocol",
]
