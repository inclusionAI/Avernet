"""Value records exchanged across the Space Skill Offline repository seam."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agentclaw.community.core.skill_center.space_skill_offline_service_protocol import (
    OfflineImpactItem,
)


@dataclass(frozen=True, slots=True)
class OfflineSkillIdentity:
    skill_id: int
    skill_uuid: str
    name: str
    sc_team_id: int | None
    latest_version_id: int
    latest_version_ordinal: int
    sc_version_number: str
    offline_at: datetime | None
    draft_target_version: int | None
    draft_status: str | None
    draft_locator: str | None


@dataclass(frozen=True, slots=True)
class OfflineInspection:
    identity: OfflineSkillIdentity
    blockers: tuple[OfflineImpactItem, ...]


@dataclass(frozen=True, slots=True)
class OfflineCommit:
    changed: bool
    target_version: int
    status: str
    locator: str


__all__ = ["OfflineCommit", "OfflineInspection", "OfflineSkillIdentity"]
