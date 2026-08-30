"""Stable OpenAPI error code and message for Space operations."""

from enum import IntEnum, StrEnum


class SpaceErrorCode(IntEnum):
    SKILL_CENTER_TEAM_CREATE_FAILED = 502201
    SKILL_GRANT_FORBIDDEN = 403203
    SKILL_GRANT_NOT_FOUND = 404201
    SKILL_GRANT_MEMBER_REQUIRED = 409301
    SKILL_GRANT_CONFLICT = 409302
    SKILL_GRANT_REASON_REQUIRED = 422201
    DRAFT_EDIT_LEASE_FORBIDDEN = 403204
    DRAFT_EDIT_LEASE_NOT_FOUND = 404202
    DRAFT_EDIT_LEASE_CONFLICT = 409303
    DRAFT_EDIT_LEASE_TOKEN_REJECTED = 409304
    IDEMPOTENCY_KEY_REUSED = 409305
    DRAFT_FROZEN = 409307
    DRAFT_REVISION_CONFLICT = 409308
    SKILL_PACKAGE_INVALID = 422202
    SKILL_NAME_CHANGED = 422203
    DRAFT_NOT_FOUND = 404204
    SKILL_PATH_INVALID = 422207
    SKILL_GIT_UNAVAILABLE = 502202
    SKILL_DRAFT_STORE_UNAVAILABLE = 503202


class SpacePublicErrorMessage(StrEnum):
    SKILL_CENTER_TEAM_CREATE_FAILED = "Skill Center team creation failed"
    SKILL_GRANT_FORBIDDEN = "Forbidden"
    SKILL_GRANT_NOT_FOUND = "Not found"
    SKILL_GRANT_MEMBER_REQUIRED = "Active Space membership required"
    SKILL_GRANT_CONFLICT = "Skill Grant state conflicts with this operation"
    SKILL_GRANT_REASON_REQUIRED = "Owner transfer reason is required"
    DRAFT_EDIT_LEASE_FORBIDDEN = "Forbidden"
    DRAFT_EDIT_LEASE_NOT_FOUND = "Not found"
    DRAFT_EDIT_LEASE_CONFLICT = "Draft edit Lease is already held"
    DRAFT_EDIT_LEASE_TOKEN_REJECTED = "Draft edit Lease fencing token was rejected"
    IDEMPOTENCY_KEY_REUSED = "Idempotency-Key was already used for another request"
    DRAFT_FROZEN = "Frozen Draft cannot be changed"
    DRAFT_REVISION_CONFLICT = "Draft revision changed"
    SKILL_PACKAGE_INVALID = "Skill package is invalid"
    SKILL_NAME_CHANGED = "SKILL.md name cannot be changed"
    DRAFT_NOT_FOUND = "Not found"
    SKILL_PATH_INVALID = "Skill file path is invalid"
    SKILL_GIT_UNAVAILABLE = "Git snapshot is unavailable"
    SKILL_DRAFT_STORE_UNAVAILABLE = "Draft content store is unavailable"
