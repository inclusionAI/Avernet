"""Stable domain values for Space Skill Publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.skill_package import ValidatedSkillPackage


class PublicationAttemptStatus(StrEnum):
    PREPARING = "PREPARING"
    SC_SUBMITTING = "SC_SUBMITTING"
    WAITING_SC = "WAITING_SC"
    MATERIALIZING = "MATERIALIZING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RESULT_UNKNOWN = "RESULT_UNKNOWN"


ACTIVE_SKILL_PUBLICATION_ATTEMPT_STATUSES = tuple(
    status.value
    for status in (
        PublicationAttemptStatus.PREPARING,
        PublicationAttemptStatus.SC_SUBMITTING,
        PublicationAttemptStatus.WAITING_SC,
        PublicationAttemptStatus.MATERIALIZING,
        PublicationAttemptStatus.RESULT_UNKNOWN,
    )
)


class PublicationRecoveryState(StrEnum):
    AUTO_RETRYING = "AUTO_RETRYING"
    AVAILABLE = "AVAILABLE"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class PublicationRecoveryKind(StrEnum):
    PREPARATION = "PREPARATION"
    SC_STATUS_CHECK = "SC_STATUS_CHECK"
    MATERIALIZATION = "MATERIALIZATION"


@dataclass(frozen=True, slots=True)
class PublicationRecovery:
    state: PublicationRecoveryState
    kind: PublicationRecoveryKind | None


@dataclass(frozen=True, slots=True)
class PublicationAttemptRecord:
    attempt_id: int
    skill_id: int
    frozen_draft_locator: str | None
    target_version: int
    status: PublicationAttemptStatus
    sc_version_number: str | None
    recovery: PublicationRecovery
    error_code: str | None
    error_message: str | None
    skill_version_id: int | None
    created_by: str
    gmt_created: datetime
    gmt_modified: datetime
    sc_post_started_at: datetime | None = None
    sc_accepted_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PublicationAttemptCreation:
    attempt: PublicationAttemptRecord
    created: bool


@dataclass(frozen=True, slots=True)
class PublicationImpactCandidate:
    owner_id: str
    bot_id: str
    bot_name: str | None
    bot: dict[str, object]


@dataclass(frozen=True, slots=True)
class PublicationImpactItem:
    owner_id: str
    bot_id: str
    bot_name: str | None


@dataclass(frozen=True, slots=True)
class PublicationWork:
    attempt: PublicationAttemptRecord
    space_id: int
    space_type: str
    sc_team_id: str | None
    skill_uuid: str
    skill_name: str
    draft_description: str
    package_url: str | None
    database_now: datetime


@dataclass(frozen=True, slots=True)
class PublicationSubmissionClaim:
    work: PublicationWork
    may_submit: bool


@dataclass(frozen=True, slots=True)
class PublicationRetryResult:
    attempt: PublicationAttemptRecord
    task_required: bool


@dataclass(frozen=True, slots=True)
class PublicationPackageStage:
    package_url: str


@runtime_checkable
class PublicationPackageStagerProtocol(Protocol):
    def stage(
        self,
        *,
        attempt_id: int,
        tenant: str,
        env: str,
        package: ValidatedSkillPackage,
    ) -> PublicationPackageStage: ...


__all__ = [
    "ACTIVE_SKILL_PUBLICATION_ATTEMPT_STATUSES",
    "PublicationAttemptCreation",
    "PublicationAttemptRecord",
    "PublicationAttemptStatus",
    "PublicationImpactCandidate",
    "PublicationImpactItem",
    "PublicationPackageStage",
    "PublicationPackageStagerProtocol",
    "PublicationRecovery",
    "PublicationRecoveryKind",
    "PublicationRecoveryState",
    "PublicationRetryResult",
    "PublicationSubmissionClaim",
    "PublicationWork",
]
