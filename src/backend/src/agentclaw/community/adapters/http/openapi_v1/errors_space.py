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
