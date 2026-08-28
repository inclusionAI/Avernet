"""Application service for Team Space Skill editor requests."""

from __future__ import annotations

from collections.abc import Callable

from agentclaw.community.core.repository.protocols.work_orders import (
    WorkOrderRepositoryProtocol,
)
from agentclaw.community.core.work_orders.errors import WorkOrderInvalidReasonError
class SpaceSkillEditorRequestService:
    def __init__(
        self,
        repository: WorkOrderRepositoryProtocol,
        env_provider: Callable[[], str],
    ) -> None:
        self._repository = repository
        self._env_provider = env_provider

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
            env=self._env_provider(),
        )


__all__ = ["SpaceSkillEditorRequestService"]
