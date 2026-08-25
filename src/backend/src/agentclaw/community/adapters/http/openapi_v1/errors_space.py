"""Stable OpenAPI error code and message for Space operations."""

from enum import IntEnum, StrEnum


class SpaceErrorCode(IntEnum):
    SKILL_CENTER_TEAM_CREATE_FAILED = 502201


class SpacePublicErrorMessage(StrEnum):
    SKILL_CENTER_TEAM_CREATE_FAILED = "Skill Center team creation failed"
