"""Service API contract for querying Skills owned by a Space.

Re-export only. The Protocol is defined in its owning core module
(``core/skill_center/space_skill_query_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.skill_center.space_skill_query_service_protocol import (
    SpaceSkillDetailRecord,
    SpaceSkillSummaryRecord,
    SpaceSkillQueryServiceProtocol,
)

__all__ = [
    "SpaceSkillQueryServiceProtocol",
    "SpaceSkillDetailRecord",
    "SpaceSkillSummaryRecord",
]
