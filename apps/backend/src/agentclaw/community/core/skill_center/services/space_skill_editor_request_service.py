"""Application service for Team Space Skill editor requests."""

from __future__ import annotations

from collections.abc import Callable

from injector import inject

from agentclaw.community.core.repository.protocols.work_orders import (
    WorkOrderRepositoryProtocol,
)
from agentclaw.community.core.work_orders.errors import WorkOrderInvalidReasonError
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.staff_dept import (
    StaffDeptPlugin,
    StaffProfileLookupError,
)
from agentclaw.community.utils.work_no import normalize_work_no_for_lookup


logger = get_logger()


class SpaceSkillEditorRequestService:
    @inject
    def __init__(
        self,
        repository: WorkOrderRepositoryProtocol,
        staff_dept: StaffDeptPlugin,
        env_provider: Callable[[], str],
    ) -> None:
        self._repository = repository
        self._staff_dept = staff_dept
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
        applicant_name = self._get_applicant_name(
            applicant_user_id=applicant_user_id
        )
        return self._repository.create_skill_editor_request(
            space_id=space_id,
            skill_id=skill_id,
            applicant_user_id=applicant_user_id,
            applicant_name=applicant_name,
            apply_reason=normalized,
            env=self._env_provider(),
        )

    def _get_applicant_name(self, *, applicant_user_id: str) -> str:
        try:
            profile = self._staff_dept.get_profile_by_work_no(
                work_no=normalize_work_no_for_lookup(applicant_user_id)
            )
        except StaffProfileLookupError:
            logger.warning(
                "failed to resolve Skill editor applicant nickname; falling back to user id",
                extra={"user_id": applicant_user_id},
                exc_info=True,
            )
            return applicant_user_id

        nickname = (profile.nick_name or "").strip()
        return nickname[:128] or applicant_user_id


__all__ = ["SpaceSkillEditorRequestService"]
