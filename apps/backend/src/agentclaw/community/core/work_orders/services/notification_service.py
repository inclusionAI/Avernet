"""Work-order notification construction and recipient-scoped queries."""

from injector import inject
from agentclaw.community.core.repository.protocols.work_orders import (
    WorkOrderRepositoryProtocol,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderInvalidRemarkError,
    WorkOrderNotificationNotFoundError,
)
from agentclaw.community.core.work_orders.models import (
    WorkOrderStatus,
    WorkOrderDetail,
    WorkOrderNotificationDraft,
    WorkOrderTitleKey,
    WorkOrderMessageContent,
    WorkOrderMessageTitle,
    WorkOrderEventType,
    WorkOrderBizType,
    NotificationCategory,
    EVENT_CATEGORIES,
)
from agentclaw.community.core.work_orders.work_order_service_protocol import (
    WorkOrderNotificationServiceProtocol,
)
from agentclaw.community.utils.env_utils import get_current_env


class WorkOrderNotificationService(WorkOrderNotificationServiceProtocol):
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
            title = WorkOrderTitleKey.SPACE_JOIN_APPROVED.value
            content = {
                "text": WorkOrderMessageContent.SPACE_JOIN_APPROVED.value.format(
                    space_name=detail.space_name
                )
            }
        elif target_status is WorkOrderStatus.REJECTED:
            if review_remark is None:
                raise WorkOrderInvalidRemarkError("review remark is required")
            title = WorkOrderTitleKey.SPACE_JOIN_REJECTED.value
            content = {
                "text": WorkOrderMessageContent.SPACE_JOIN_REJECTED.value.format(
                    space_name=detail.space_name,
                    review_remark=review_remark,
                )
            }
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

    @staticmethod
    def build_bot_editor_review_result(
        *,
        detail: WorkOrderDetail,
        bot_name: str,
        target_status: WorkOrderStatus,
        review_remark: str | None,
    ) -> WorkOrderNotificationDraft:
        if target_status is WorkOrderStatus.APPROVED:
            title = WorkOrderMessageTitle.BOT_COLLABORATOR_APPROVED.value
            content = WorkOrderMessageContent.BOT_COLLABORATOR_APPROVED.value.format(
                bot_name=bot_name
            )
        elif target_status is WorkOrderStatus.REJECTED:
            if review_remark is None:
                raise WorkOrderInvalidRemarkError("review remark is required")
            title = WorkOrderMessageTitle.BOT_COLLABORATOR_REJECTED.value
            content = WorkOrderMessageContent.BOT_COLLABORATOR_REJECTED.value.format(
                bot_name=bot_name, review_remark=review_remark
            )
        else:
            raise ValueError(f"unsupported review status: {target_status}")
        return WorkOrderNotificationDraft(
            recipient_user_id=detail.work_order.applicant_user_id,
            notification_category=NotificationCategory.NOTICE,
            event_type=WorkOrderEventType.BOT_COLLABORATOR_REVIEWED,
            biz_type=WorkOrderBizType.BOT_COLLABORATOR,
            biz_id=detail.work_order.biz_id,
            title=title,
            content={"text": content},
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
