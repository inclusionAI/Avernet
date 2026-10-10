"""Service API for Team Space Skill editor applications."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.editor_request_contract import (
    SkillEditorRequestResult,
)


@runtime_checkable
class SpaceSkillEditorRequestServiceProtocol(Protocol):
    @abstractmethod
    def get_approval_policy(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> bool: ...

    @abstractmethod
    def update_approval_policy(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        auto_approve_editor_requests: bool,
    ) -> bool: ...

    @abstractmethod
    def create_request(
        self,
        *,
        space_id: int,
        skill_id: int,
        applicant_user_id: str,
        reason: str,
    ) -> SkillEditorRequestResult: ...


__all__ = ["SpaceSkillEditorRequestServiceProtocol", "SkillEditorRequestResult"]
