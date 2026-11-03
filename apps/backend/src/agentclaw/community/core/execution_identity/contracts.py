"""Domain contracts for a Bot's persistent execution identity."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ExecutionIdentityType(StrEnum):
    STAFF = "STAFF"
    DIGITAL_EMPLOYEE = "DIGITAL_EMPLOYEE"


class ExecutionIdentityStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class OwnerHandoffExecutionPlan:
    """Read-only plan; never changes Bot ownership or issues credentials."""

    bot_pk: int
    bot_id: str
    entity_id: str
    current_owner_id: str
    new_owner_id: str
    execution_workno: str
    requires_reissue: bool


@dataclass(frozen=True)
class ExecutionIdentityBinding:
    id: int
    bot_pk: int
    execution_workno: str
    identity_type: ExecutionIdentityType
    status: ExecutionIdentityStatus
    authorization_id: str | None = None
    credential_id: str | None = None
    agent_id: str | None = None
    credential_status: str | None = None


class ExecutionIdentityError(Exception):
    """Base error for execution identity transitions."""


class ExecutionIdentityNotFoundError(ExecutionIdentityError):
    pass


class ExecutionIdentityChangeInProgressError(ExecutionIdentityError):
    pass


class ExecutionIdentityOperationNotAllowedError(ExecutionIdentityError):
    pass


__all__ = [
    "OwnerHandoffExecutionPlan",
    "ExecutionIdentityBinding",
    "ExecutionIdentityChangeInProgressError",
    "ExecutionIdentityError",
    "ExecutionIdentityNotFoundError",
    "ExecutionIdentityOperationNotAllowedError",
    "ExecutionIdentityStatus",
    "ExecutionIdentityType",
]
