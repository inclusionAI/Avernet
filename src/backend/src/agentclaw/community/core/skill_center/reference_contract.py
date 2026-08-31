"""Stable domain values for SC Public Reference operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class SkillCenterReferenceStatus(StrEnum):
    QUEUED = "QUEUED"
    RESOLVING_VERSION = "RESOLVING_VERSION"
    MATERIALIZING = "MATERIALIZING"
    ADDING_TO_SKILL_SET = "ADDING_TO_SKILL_SET"
    PROJECTING_RUNTIME = "PROJECTING_RUNTIME"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


TERMINAL_REFERENCE_STATUSES = frozenset(
    {SkillCenterReferenceStatus.COMPLETED, SkillCenterReferenceStatus.FAILED}
)


class ReferenceIdempotencyConflictError(RuntimeError):
    """One Idempotency-Key was reused for a different Reference command."""


class ReferenceBatchSizeError(ValueError):
    """A Reference command contains no code or more than twenty unique codes."""


class ReferenceValidationError(ValueError):
    """A Reference command carries a malformed key or external Skill code."""


class ReferenceTaskUnavailableError(RuntimeError):
    """Operations were persisted, but their durable task could not be ensured."""


class ReferenceNotFoundError(LookupError):
    """The frozen Bot/SkillSet scope cannot see the requested Reference item."""


@dataclass(frozen=True, slots=True)
class SkillCenterReferenceItem:
    reference_id: str
    request_id: str
    skill_set_id: str
    skill_code: str
    sc_version_number: str | None
    status: SkillCenterReferenceStatus
    skill_id: str | None
    error_code: str | None
    error_message: str | None
    gmt_created: datetime
    gmt_modified: datetime


@dataclass(frozen=True, slots=True)
class SkillCenterReferenceBatch:
    request_id: str
    bot_id: str
    owner_id: str
    skill_set_id: str
    actor_id: str
    items: tuple[SkillCenterReferenceItem, ...]


@dataclass(frozen=True, slots=True)
class SkillCenterReferenceCreateResult:
    batch: SkillCenterReferenceBatch
    created: bool


@dataclass(frozen=True, slots=True)
class SkillCenterReferencePage:
    total: int
    items: tuple[SkillCenterReferenceItem, ...]


__all__ = [
    "ReferenceBatchSizeError",
    "ReferenceIdempotencyConflictError",
    "ReferenceNotFoundError",
    "ReferenceTaskUnavailableError",
    "ReferenceValidationError",
    "SkillCenterReferenceBatch",
    "SkillCenterReferenceCreateResult",
    "SkillCenterReferenceItem",
    "SkillCenterReferencePage",
    "SkillCenterReferenceStatus",
    "TERMINAL_REFERENCE_STATUSES",
]
