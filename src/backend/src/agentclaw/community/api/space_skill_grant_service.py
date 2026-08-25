"""Public re-export of the core-owned Space Skill Grant Service API."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.services.space_skill_grant_protocol import (
    SpaceSkillGrantServiceProtocol as CoreSpaceSkillGrantServiceProtocol,
)


@runtime_checkable
class SpaceSkillGrantServiceProtocol(CoreSpaceSkillGrantServiceProtocol, Protocol):
    """Adapter-facing alias of the core-owned Grant service contract."""


__all__ = ["SpaceSkillGrantServiceProtocol"]
