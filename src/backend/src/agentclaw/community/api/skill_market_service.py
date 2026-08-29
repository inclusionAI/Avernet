"""Service API for the built-in Skill marketplace.

Re-export only. The Protocol is defined in its owning core module
(``core/skill_center/skill_market_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.skill_center.skill_market_service_protocol import (
    SkillMarketSearchQuery,
    SkillMarketSearchResult,
    SkillMarketServiceProtocol,
)

__all__ = [
    "SkillMarketSearchQuery",
    "SkillMarketSearchResult",
    "SkillMarketServiceProtocol",
]
