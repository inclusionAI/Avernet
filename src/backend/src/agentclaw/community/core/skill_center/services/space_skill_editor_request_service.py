"""Application service for Team Space Skill editor requests."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.work_orders import (
    WorkOrderRepositoryProtocol,
)
from agentclaw.community.core.work_orders.errors import WorkOrderInvalidReasonError
from agentclaw.community.utils.env_utils import get_current_env


class SpaceSkillEditorRequestService:
    @inject
    def __init__(self, repository: WorkOrderRepositoryProtocol) -> None:
        self._repository = repository

    def create_request(
        self,
        *,
        space_id: int,
        skill_id: int,
        applicant_user_id: str,
        reason: str,
    ):
        normalized = reason.strip()
        if not normalized or len(normalized) > 512:
            raise WorkOrderInvalidReasonError("reason must contain 1-512 characters")
        return self._repository.create_skill_editor_request(
            space_id=space_id,
            skill_id=skill_id,
            applicant_user_id=applicant_user_id,
            applicant_name=applicant_user_id,
            apply_reason=normalized,
            env=get_current_env(),
        )


__all__ = ["SpaceSkillEditorRequestService"]
