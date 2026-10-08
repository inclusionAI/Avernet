"""Service API for Team Space Skill editor applications."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.work_orders.models import WorkOrderRecord


@runtime_checkable
class SpaceSkillEditorRequestServiceProtocol(Protocol):
    @abstractmethod
    def create_request(
        self,
        *,
        space_id: int,
        skill_id: int,
        applicant_user_id: str,
        reason: str,
    ) -> WorkOrderRecord: ...


__all__ = ["SpaceSkillEditorRequestServiceProtocol"]
