"""Skill-owned Work Order approval policy and presentation."""

from __future__ import annotations

import json
from collections.abc import Callable

from agentclaw.community.core.repository.protocols.work_orders import (
    WorkOrderRepositoryProtocol,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderInvalidRemarkError,
    WorkOrderSkillEditorRequestNotAllowedError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderBizType,
    WorkOrderDetail,
    WorkOrderEventType,
    WorkOrderMessageContent,
    WorkOrderMessageTitle,
    WorkOrderNotificationDraft,
    WorkOrderStatus,
)
from agentclaw.community.core.work_orders.protocols import (
    SkillCollaboratorApprovalHandlerProtocol,
)
class SkillCollaboratorApprovalHandler(SkillCollaboratorApprovalHandlerProtocol):
    def __init__(
        self,
        repository: WorkOrderRepositoryProtocol,
        env_provider: Callable[[], str],
    ) -> None:
        self._repository = repository
        self._env_provider = env_provider

    def process(
        self,
        *,
        detail: WorkOrderDetail,
        actor_id: str,
        review_remark: str | None,
        target_status: WorkOrderStatus,
    ):
        try:
            data = json.loads(detail.work_order.biz_data or "{}")
            skill_name = str(data["skill_name"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WorkOrderSkillEditorRequestNotAllowedError(
                "work-order Skill identity is invalid"
            ) from exc
        if target_status is WorkOrderStatus.APPROVED:
            title = WorkOrderMessageTitle.SKILL_COLLABORATOR_APPROVED.value
            content = WorkOrderMessageContent.SKILL_COLLABORATOR_APPROVED.value.format(
                skill_name=skill_name
            )
        elif target_status is WorkOrderStatus.REJECTED:
            if review_remark is None:
                raise WorkOrderInvalidRemarkError("review remark is required")
            title = WorkOrderMessageTitle.SKILL_COLLABORATOR_REJECTED.value
            content = WorkOrderMessageContent.SKILL_COLLABORATOR_REJECTED.value.format(
                skill_name=skill_name,
                review_remark=review_remark,
            )
        else:
            raise ValueError(f"unsupported review status: {target_status}")
        notification = WorkOrderNotificationDraft(
            recipient_user_id=detail.work_order.applicant_user_id,
            notification_category=NotificationCategory.NOTICE,
            event_type=WorkOrderEventType.SKILL_COLLABORATOR_REVIEWED,
            biz_type=WorkOrderBizType.SKILL_COLLABORATOR,
            biz_id=detail.work_order.biz_id,
            title=title,
            content={"text": content},
        )
        return self._repository.review_skill_editor_request(
            work_order_id=detail.work_order.id,
            reviewer_user_id=actor_id,
            review_remark=review_remark,
            target_status=target_status,
            notification=notification,
            env=self._env_provider(),
        )


__all__ = ["SkillCollaboratorApprovalHandler"]
