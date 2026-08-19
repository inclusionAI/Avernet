"""Service API for the built-in Skill marketplace."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.market_contracts import (
    SkillMarketSearchQuery,
    SkillMarketSearchResult,
)


@runtime_checkable
class SkillMarketServiceProtocol(Protocol):
    """Read-only application service for the built-in Skill marketplace."""

    def search(self, query: SkillMarketSearchQuery) -> SkillMarketSearchResult: ...


__all__ = [
    "SkillMarketSearchQuery",
    "SkillMarketSearchResult",
    "SkillMarketServiceProtocol",
]
