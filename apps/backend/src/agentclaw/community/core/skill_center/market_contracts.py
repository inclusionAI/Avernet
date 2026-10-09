"""Transport-neutral contracts for built-in Skill marketplace queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SkillMarketSearchQuery:
    """Search criteria for the built-in Skill marketplace."""

    keyword: str
    page_num: int
    page_size: int


@dataclass(frozen=True)
class SkillMarketSearchResult:
    """One page returned by the built-in Skill marketplace."""

    total: int
    items: tuple[dict[str, Any], ...]
