"""Service API Protocol for skill batch sync.

Re-export only. The Protocol is defined in its owning core module
(``core/skill_center/skill_batch_sync_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.skill_center.skill_batch_sync_service_protocol import (
    SkillBatchSyncServiceProtocol,
)

__all__ = [
    "SkillBatchSyncServiceProtocol",
]
