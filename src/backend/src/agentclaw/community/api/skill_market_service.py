"""Service API for the built-in OCB Skill marketplace."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class SkillMarketSearchQuery:
    """Transport-neutral query for the tenant-wide built-in Skill market."""

    keyword: str
    page_num: int
    page_size: int


@dataclass(frozen=True)
class SkillMarketSearchResult:
    """One page returned by the built-in Skill marketplace."""

    total: int
    items: tuple[dict[str, Any], ...]


@runtime_checkable
class SkillMarketServiceProtocol(Protocol):
    """Read-only application service for the built-in Skill marketplace."""

    def search(self, query: SkillMarketSearchQuery) -> SkillMarketSearchResult: ...
