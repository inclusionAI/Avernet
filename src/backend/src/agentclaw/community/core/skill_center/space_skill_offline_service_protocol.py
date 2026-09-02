"""Service API contract for recoverable Space Skill Offline."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class OfflineBlockerKind(StrEnum):
    DRAFT = "DRAFT"
    PUBLICATION = "PUBLICATION"
    MEMBERSHIP = "MEMBERSHIP"
    INSTALLATION = "INSTALLATION"
    SERVICE_ARTIFACT = "SERVICE_ARTIFACT"
    UNKNOWN_ARTIFACT = "UNKNOWN_ARTIFACT"


@dataclass(frozen=True, slots=True)
class OfflineImpactItem:
    kind: OfflineBlockerKind
    resource_id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class OfflineImpact:
    blocked: bool
    total: int
    counts: dict[str, int]
    items: tuple[OfflineImpactItem, ...]
    warnings: tuple[OfflineImpactItem, ...] = ()


@dataclass(frozen=True, slots=True)
class OfflineDraft:
    target_version: int
    status: str
    revision_id: str


@dataclass(frozen=True, slots=True)
class SpaceSkillOfflineResult:
    changed: bool
    lifecycle_status: str
    draft: OfflineDraft


@runtime_checkable
class SpaceSkillOfflineServiceProtocol(Protocol):
    @abstractmethod
    def impact(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        page: int,
        page_size: int,
    ) -> OfflineImpact: ...

    @abstractmethod
    def offline(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> SpaceSkillOfflineResult: ...


__all__ = [
    "OfflineBlockerKind",
    "OfflineDraft",
    "OfflineImpact",
    "OfflineImpactItem",
    "SpaceSkillOfflineResult",
    "SpaceSkillOfflineServiceProtocol",
]
