"""Contract and immutable values for exact Skill Version resolution."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset


class SkillVersionResolutionError(ValueError):
    """A Center asset has no complete, consumable PUBLISHED Version."""


@dataclass(frozen=True, slots=True)
class PublishedSkillVersion:
    """Exact immutable version metadata used outside latest resolution."""

    skill_version_id: int
    skill_id: int
    version_ordinal: int
    sc_version_number: str
    sc_skill_id: int | None
    sc_version_id: int | None
    name: str
    description: str | None
    mcp_dependencies: tuple[object, ...]
    published_at: datetime | None


@runtime_checkable
class SkillVersionResolverProtocol(Protocol):
    """Resolve latest or exact PUBLISHED Versions without side effects."""

    def resolve_latest_runtime_assets(
        self,
        *,
        env: str,
        assets: Sequence[RegisteredSkillAsset],
    ) -> tuple[RegisteredSkillAsset, ...]: ...

    def resolve_exact_published(
        self,
        *,
        env: str,
        skill_id: int,
        skill_version_id: int,
    ) -> PublishedSkillVersion: ...


__all__ = [
    "PublishedSkillVersion",
    "SkillVersionResolutionError",
    "SkillVersionResolverProtocol",
]
