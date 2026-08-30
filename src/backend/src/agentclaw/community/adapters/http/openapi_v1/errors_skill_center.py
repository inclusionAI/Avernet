"""Transport mappings for G4 Reference and materialized-public Sync errors."""

from agentclaw.community.core.skill_center.reference_contract import (
    ReferenceBatchSizeError,
    ReferenceIdempotencyConflictError,
    ReferenceNotFoundError,
    ReferenceTaskUnavailableError,
    ReferenceValidationError,
)
from agentclaw.community.core.skill_center.skill_center_sync_contract import (
    SkillCenterSyncInProgressError,
)


SKILL_CENTER_ENVELOPE_ERRORS: dict[type[Exception], tuple[int, str]] = {
    ReferenceBatchSizeError: (
        422,
        "Reference requires between one and twenty unique Skills",
    ),
    ReferenceIdempotencyConflictError: (
        409,
        "Idempotency key was reused with a different request",
    ),
    ReferenceNotFoundError: (404, "Not found"),
    ReferenceTaskUnavailableError: (
        503,
        "Reference task service is temporarily unavailable",
    ),
    ReferenceValidationError: (422, "Invalid Reference request"),
    SkillCenterSyncInProgressError: (
        409,
        "Skill Center synchronization is already in progress",
    ),
}

SKILL_CENTER_ENVELOPE_ERROR_CODES: dict[type[Exception], int] = {
    ReferenceBatchSizeError: 422204,
    ReferenceIdempotencyConflictError: 409305,
    ReferenceTaskUnavailableError: 503000,
    SkillCenterSyncInProgressError: 409314,
}


__all__ = [
    "SKILL_CENTER_ENVELOPE_ERROR_CODES",
    "SKILL_CENTER_ENVELOPE_ERRORS",
]
