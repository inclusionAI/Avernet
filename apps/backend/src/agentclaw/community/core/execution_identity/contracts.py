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
    "ExecutionIdentityBinding",
    "ExecutionIdentityChangeInProgressError",
    "ExecutionIdentityError",
    "ExecutionIdentityNotFoundError",
    "ExecutionIdentityOperationNotAllowedError",
    "ExecutionIdentityStatus",
    "ExecutionIdentityType",
]
