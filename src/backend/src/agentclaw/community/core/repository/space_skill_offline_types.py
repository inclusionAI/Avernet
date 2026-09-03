"""Value records exchanged across the Space Skill Offline repository seam."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

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
class OfflinePublicationAttemptFact:
    id: int
    target_version_ordinal: int
    status: str


@dataclass(frozen=True, slots=True)
class OfflineMembershipFact:
    id: int
    skill_set_name: str


@dataclass(frozen=True, slots=True)
class OfflineInstallationFact:
    id: int
    bot_id: str


@dataclass(frozen=True, slots=True)
class OfflineInspection:
    """Raw persistence facts observed in one repository session."""

    identity: OfflineSkillIdentity
    space_bound: bool
    actor_roles: tuple[str, ...]
    publication_attempts: tuple[OfflinePublicationAttemptFact, ...]
    memberships: tuple[OfflineMembershipFact, ...]
    installations: tuple[OfflineInstallationFact, ...]


@dataclass(frozen=True, slots=True)
class OfflineCommit:
    changed: bool
    offline_at: datetime


__all__ = [
    "OfflineCommit",
    "OfflineInspection",
    "OfflineInstallationFact",
    "OfflineMembershipFact",
    "OfflinePublicationAttemptFact",
    "OfflineSkillIdentity",
]
