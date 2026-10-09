"""Application service for Team Space Skill editor requests."""

from __future__ import annotations

from collections.abc import Callable

from injector import inject

from agentclaw.community.core.repository.protocols.work_orders import (
    WorkOrderRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillEditorRequestRepositoryProtocol,
)
from agentclaw.community.core.skill_center.editor_request_contract import (
    SkillEditorRequestResult,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderInvalidReasonError,
    WorkOrderSkillEditorRequestNotAllowedError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalMode,
    WorkOrderBizType,
    WorkOrderEventStatus,
    WorkOrderEventType,
    WorkOrderMessageTitle,
    WorkOrderStatus,
)
from agentclaw.community.core.work_orders.work_order_service_protocol import (
    WorkOrderServiceProtocol,
)
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
        skill_repository: SkillEditorRequestRepositoryProtocol,
        work_orders: WorkOrderServiceProtocol,
        staff_dept: StaffDeptPlugin,
        env_provider: Callable[[], str],
    ) -> None:
        self._repository = repository
        self._skill_repository = skill_repository
        self._work_orders = work_orders
        self._staff_dept = staff_dept
        self._env_provider = env_provider

    def get_approval_policy(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> bool:
        return self._skill_repository.get_editor_approval_policy(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            env=self._env_provider(),
        )

    def update_approval_policy(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        auto_approve_editor_requests: bool,
    ) -> bool:
        return self._skill_repository.update_editor_approval_policy(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            auto_approve_editor_requests=auto_approve_editor_requests,
            env=self._env_provider(),
        )

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
        admission = self._skill_repository.inspect_editor_request(
            space_id=space_id,
            skill_id=skill_id,
            applicant_user_id=applicant_user_id,
            env=self._env_provider(),
        )
        if admission.auto_approve:
            result = self._work_orders.create_work_order_event(
                event_category=NotificationCategory.APPROVAL,
                approval_mode=WorkOrderApprovalMode.AUTO,
                biz_type=WorkOrderBizType.SKILL_COLLABORATOR.value,
                biz_id=str(skill_id),
                event_type=WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
                applicant_user_id=applicant_user_id,
                approver_user_ids=[],
                recipient_user_ids=[applicant_user_id],
                title=WorkOrderMessageTitle.SKILL_COLLABORATOR_PENDING.value,
                content=None,
                apply_reason=normalized,
                biz_data={
                    "space_id": space_id,
                    "skill_id": skill_id,
                    "skill_name": admission.skill_name,
                },
                actor_id=applicant_user_id,
            )
            if (
                result.status is not WorkOrderEventStatus.APPROVED
                or result.work_order_id is None
                or result.work_order_no is None
            ):
                raise WorkOrderSkillEditorRequestNotAllowedError(
                    "automatic Skill editor approval did not complete"
                )
            return SkillEditorRequestResult(
                work_order_id=result.work_order_id,
                work_order_no=result.work_order_no,
                status=WorkOrderStatus.APPROVED,
            )

        applicant_name = self._get_applicant_name(applicant_user_id=applicant_user_id)
        order = self._repository.create_skill_editor_request(
            space_id=space_id,
            skill_id=skill_id,
            applicant_user_id=applicant_user_id,
            applicant_name=applicant_name,
            apply_reason=normalized,
            env=self._env_provider(),
        )
        return SkillEditorRequestResult(
            work_order_id=order.id,
            work_order_no=order.work_order_no,
            status=order.status,
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
