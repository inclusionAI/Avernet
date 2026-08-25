"""Stable OpenAPI error code and message for Space operations."""

from enum import IntEnum, StrEnum


class SpaceErrorCode(IntEnum):
    SKILL_CENTER_TEAM_CREATE_FAILED = 502201
    SKILL_GRANT_FORBIDDEN = 403203
    SKILL_GRANT_NOT_FOUND = 404201
    SKILL_GRANT_MEMBER_REQUIRED = 409301
    SKILL_GRANT_CONFLICT = 409302
    SKILL_GRANT_REASON_REQUIRED = 422201


class SpacePublicErrorMessage(StrEnum):
    SKILL_CENTER_TEAM_CREATE_FAILED = "Skill Center team creation failed"
    SKILL_GRANT_FORBIDDEN = "Forbidden"
    SKILL_GRANT_NOT_FOUND = "Not found"
    SKILL_GRANT_MEMBER_REQUIRED = "Active Space membership required"
    SKILL_GRANT_CONFLICT = "Skill Grant state conflicts with this operation"
    SKILL_GRANT_REASON_REQUIRED = "Owner transfer reason is required"
