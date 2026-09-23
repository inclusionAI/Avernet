"""Persistence contract for Team Space Skill editor approval policy."""

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.repository.protocols.skill_center_types import (
    SkillEditorApprovalPolicyRecord,
)


@runtime_checkable
class SkillEditorApprovalPolicyRepositoryProtocol(Protocol):
    @abstractmethod
    def get_policy(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> SkillEditorApprovalPolicyRecord: ...

    @abstractmethod
    def update_policy(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        auto_approve_editor_requests: bool,
        env: str,
    ) -> SkillEditorApprovalPolicyRecord: ...


__all__ = ["SkillEditorApprovalPolicyRepositoryProtocol"]
