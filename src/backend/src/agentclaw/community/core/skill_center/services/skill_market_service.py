"""Read-only query service for the built-in OCB Skill marketplace."""
from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.api.skill_market_service import (
    SkillMarketSearchQuery,
    SkillMarketSearchResult,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.skill_center.services.skill_cache import MarketCache


class SkillMarketService:
    """Search tenant-visible ``git://`` Skills without a Bot/path context."""

    _CACHE_KEY = "market_skills_list_default"

    @inject
    def __init__(self, skill_repo: SkillRepository, market_cache: MarketCache) -> None:
        self._skill_repo = skill_repo
        self._market_cache = market_cache

    def search(self, query: SkillMarketSearchQuery) -> SkillMarketSearchResult:
        skills = self._market_cache.get(self._CACHE_KEY)
        if not isinstance(skills, list):
            skills = self._load_market_skills()
            self._market_cache.set(self._CACHE_KEY, skills)

        keyword = query.keyword.strip().lower()
        matches = [
            item
            for item in skills
            if isinstance(item, dict) and self._matches(item, keyword)
        ]
        start = (query.page_num - 1) * query.page_size
        end = start + query.page_size
        return SkillMarketSearchResult(
            total=len(matches),
            items=tuple(matches[start:end]),
        )

    def _load_market_skills(self) -> list[dict[str, Any]]:
        rows = self._skill_repo.list_skills(user_id=None, bolt_id=None)
        return [
            row
            for row in rows
            if isinstance(row, dict)
            and str(row.get("git_path") or "").startswith("git://")
        ]

    @staticmethod
    def _matches(item: dict[str, Any], keyword: str) -> bool:
        if not keyword:
            return True
        return any(
            keyword in str(item.get(field) or "").lower()
            for field in ("name", "description", "category")
        )
