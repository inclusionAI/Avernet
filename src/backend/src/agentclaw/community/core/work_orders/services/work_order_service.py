"""Transport-agnostic work-order orchestration and authorization."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.spaces import SpaceRepositoryProtocol
from agentclaw.community.core.repository.protocols.work_orders import (
    WorkOrderRepositoryProtocol,
)
from agentclaw.community.core.spaces.errors import SpaceAccessDeniedError
from agentclaw.community.core.spaces.models import SpaceType
from agentclaw.community.core.spaces.services.space_access_service import (
    SpaceAccessService,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderApplicantAlreadyMemberError,
    WorkOrderInvalidReasonError,
    WorkOrderInvalidRemarkError,
    WorkOrderJoinNotAllowedError,
    WorkOrderNotificationNotFoundError,
    WorkOrderNotFoundError,
)
from agentclaw.community.core.work_orders.models import (
    EVENT_CATEGORIES,
    WorkOrderDetail,
    WorkOrderEventType,
    WorkOrderItemType,
    WorkOrderMessageContent,
    WorkOrderMessageTitle,
    WorkOrderNotificationDraft,
    WorkOrderQueryType,
    WorkOrderStatus,
)
from agentclaw.community.utils.env_utils import get_current_env


class WorkOrderService:
    @inject
    def __init__(
        self,
        repository: WorkOrderRepositoryProtocol,
        spaces: SpaceRepositoryProtocol,
        access: SpaceAccessService,
        notifications: WorkOrderNotificationService,
    ) -> None:
        self._repository = repository
        self._spaces = spaces
        self._access = access
        self._notifications = notifications

    @staticmethod
    def _required_text(value: str, *, limit: int, error: type[Exception]) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > limit:
            raise error(f"value must contain 1-{limit} characters")
        return normalized

    def create_space_join_request(
        self, *, space_id: int, applicant_user_id: str, reason: str
    ):
        reason = self._required_text(
            reason, limit=512, error=WorkOrderInvalidReasonError
        )
        space = self._access.require_space(space_id=space_id)
        if space.space_type is not SpaceType.TEAM:
            raise WorkOrderJoinNotAllowedError(
                "personal spaces do not accept join requests"
            )
        if (
            self._spaces.get_member(
                space_id=space_id,
                user_id=applicant_user_id,
                env=get_current_env(),
            )
            is not None
        ):
            raise WorkOrderApplicantAlreadyMemberError(
                "applicant is already a space member"
            )
        return self._repository.create_space_join_request(
            space_id=space_id,
            applicant_user_id=applicant_user_id,
            applicant_name=applicant_user_id,
            apply_reason=reason,
            env=get_current_env(),
        )

    def list_items(
        self,
        *,
        actor_id: str,
        query_type: WorkOrderQueryType,
        item_type: WorkOrderItemType,
        page_no: int,
        page_size: int,
    ):
        return self._repository.list_items(
            actor_id=actor_id,
            env=get_current_env(),
            query_type=query_type,
            item_type=item_type,
            offset=(page_no - 1) * page_size,
            limit=page_size,
        )

    def get_detail(self, *, work_order_id: int, actor_id: str):
        detail = self._repository.get_detail(
            work_order_id=work_order_id,
            actor_id=actor_id,
            env=get_current_env(),
        )
        if detail is None:
            raise WorkOrderNotFoundError("work order not found")
        return detail

    def approve(self, *, work_order_id: int, actor_id: str, review_remark: str | None):
        normalized_remark = (review_remark or "").strip() or None
        if normalized_remark is not None and len(normalized_remark) > 512:
            raise WorkOrderInvalidRemarkError(
                "value must contain no more than 512 characters"
            )
        return self._review(
            work_order_id=work_order_id,
            actor_id=actor_id,
            review_remark=normalized_remark,
            target_status=WorkOrderStatus.APPROVED,
        )

    def reject(self, *, work_order_id: int, actor_id: str, review_remark: str | None):
        normalized_remark = self._required_text(
            review_remark or "", limit=512, error=WorkOrderInvalidRemarkError
        )
        return self._review(
            work_order_id=work_order_id,
            actor_id=actor_id,
            review_remark=normalized_remark,
            target_status=WorkOrderStatus.REJECTED,
        )

    def _review(
        self,
        *,
        work_order_id: int,
        actor_id: str,
        review_remark: str | None,
        target_status: WorkOrderStatus,
    ):
        detail = self.get_detail(work_order_id=work_order_id, actor_id=actor_id)
        try:
            self._access.require_space_owner(space_id=detail.space_id, user_id=actor_id)
        except SpaceAccessDeniedError as exc:
            raise WorkOrderAccessDeniedError("space owner role required") from exc
        notification = self._notifications.build_space_join_review_result(
            detail=detail,
            target_status=target_status,
            review_remark=review_remark,
        )
        return self._repository.review_space_join(
            work_order_id=work_order_id,
            reviewer_user_id=actor_id,
            review_remark=review_remark,
            target_status=target_status,
            notification=notification,
            env=get_current_env(),
        )


class WorkOrderNotificationService:
    @inject
    def __init__(self, repository: WorkOrderRepositoryProtocol) -> None:
        self._repository = repository

    @staticmethod
    def build_space_join_review_result(
        *,
        detail: WorkOrderDetail,
        target_status: WorkOrderStatus,
        review_remark: str | None,
    ) -> WorkOrderNotificationDraft:
        if target_status is WorkOrderStatus.APPROVED:
            title = WorkOrderMessageTitle.SPACE_JOIN_APPROVED.value
            content = WorkOrderMessageContent.SPACE_JOIN_APPROVED.value.format(
                space_name=detail.space_name
            )
        elif target_status is WorkOrderStatus.REJECTED:
            if review_remark is None:
                raise WorkOrderInvalidRemarkError("review remark is required")
            title = WorkOrderMessageTitle.SPACE_JOIN_REJECTED.value
            content = WorkOrderMessageContent.SPACE_JOIN_REJECTED.value.format(
                space_name=detail.space_name,
                review_remark=review_remark,
            )
        else:
            raise ValueError(f"unsupported review status: {target_status}")

        event_type = WorkOrderEventType.SPACE_JOIN_REVIEWED
        return WorkOrderNotificationDraft(
            recipient_user_id=detail.work_order.applicant_user_id,
            notification_category=EVENT_CATEGORIES[event_type],
            event_type=event_type,
            biz_type=detail.work_order.biz_type,
            biz_id=detail.work_order.biz_id,
            title=title,
            content=content,
        )

    def get_detail(self, *, notification_id: int, actor_id: str):
        result = self._repository.get_notification(
            notification_id=notification_id,
            recipient_user_id=actor_id,
            env=get_current_env(),
            mark_read=True,
        )
        if result is None:
            raise WorkOrderNotificationNotFoundError("notification not found")
        return result

    def unread_count(self, *, actor_id: str) -> int:
        return self._repository.count_unread(
            recipient_user_id=actor_id, env=get_current_env()
        )

    def badge_summary(self, *, actor_id: str):
        return self._repository.get_notification_badge_summary(
            recipient_user_id=actor_id, env=get_current_env()
        )

    def mark_read(self, *, notification_id: int, actor_id: str):
        record = self._repository.mark_notification_read(
            notification_id=notification_id,
            recipient_user_id=actor_id,
            env=get_current_env(),
        )
        if record is None:
            raise WorkOrderNotificationNotFoundError("notification not found")
        return record

    def mark_all_read(self, *, actor_id: str) -> int:
        return self._repository.mark_all_notifications_read(
            recipient_user_id=actor_id, env=get_current_env()
        )
