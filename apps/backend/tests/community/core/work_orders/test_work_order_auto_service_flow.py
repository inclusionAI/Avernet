"""AUTO mode orchestration tests for work-order service."""

from unittest.mock import MagicMock

from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalMode,
    WorkOrderBizType,
    WorkOrderEventCreatedResult,
    WorkOrderEventStatus,
    WorkOrderEventType,
    WorkOrderStatus,
)
from agentclaw.community.core.work_orders.services.work_order_service import WorkOrderService
from agentclaw.community.core.work_orders.callbacks import WorkOrderDecisionCallbackDispatcher
from agentclaw.community.core.work_orders.errors import WorkOrderCallbackError
from agentclaw.community.core.work_orders.models import SYSTEM_REVIEWER_USER_ID
from agentclaw.community.core.work_orders.services.work_order_service import WorkOrderService
from tests.community.core.work_orders.test_work_order_service import _service


def _auto_request(service, biz_type, event_type, biz_id):
    return service.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        approval_mode=WorkOrderApprovalMode.AUTO,
        biz_type=biz_type,
        biz_id=biz_id,
        event_type=event_type,
        applicant_user_id="actor",
        approver_user_ids=["notify-user"],
        recipient_user_ids=[],
        title="AUTO request",
        content=None,
        apply_reason=None,
        biz_data={"request_ids": ["request-1"]} if biz_type == WorkOrderBizType.BOT_FRIEND.value else {},
        actor_id="actor",
    )


def test_auto_space_join_service_finalizes_and_notifies_after_callback():
    service, repo, *_ = _service()
    repo.create_work_order_event.return_value = WorkOrderEventCreatedResult(
        event_category=NotificationCategory.APPROVAL,
        work_order_id=17,
        work_order_no="WO-17",
        notification_ids=[],
        status=WorkOrderEventStatus.PENDING,
    )

    result = _auto_request(
        service,
        WorkOrderBizType.SPACE_JOIN.value,
        WorkOrderEventType.SPACE_JOIN_APPLIED.value,
        "space-1",
    )

    assert result.status is WorkOrderEventStatus.APPROVED
    repo.claim_auto_approval.assert_called_once_with(
        work_order_id=17, reviewer_user_id=SYSTEM_REVIEWER_USER_ID, env="dev"
    )
    repo.apply_auto_space_join.assert_called_once_with(work_order_id=17, env="dev")
    repo.finalize_auto_approval.assert_called_once_with(work_order_id=17, env="dev")
    repo.create_auto_result_notifications.assert_called_once()
    assert repo.create_auto_result_notifications.call_args.kwargs["status"] is WorkOrderStatus.APPROVED


def test_auto_callback_exception_marks_failed_and_sends_failure_result():
    callbacks = MagicMock(spec=WorkOrderDecisionCallbackDispatcher)
    callbacks.requires_callback.return_value = True
    service, repo, *_ = _service(decision_callbacks=callbacks)
    repo.create_work_order_event.return_value = WorkOrderEventCreatedResult(
        event_category=NotificationCategory.APPROVAL,
        work_order_id=18,
        work_order_no="WO-18",
        notification_ids=[],
        status=WorkOrderEventStatus.PENDING,
    )
    repo.get_approval_context.return_value.model_copy.return_value = object()
    callbacks.dispatch.side_effect = WorkOrderCallbackError("callback broke")

    result = _auto_request(
        service,
        WorkOrderBizType.BOT_FRIEND.value,
        WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED.value,
        "friend-1",
    )

    assert result.status is WorkOrderEventStatus.FAILED
    repo.mark_auto_approval_failed.assert_called_once()
    assert "callback broke" in repo.mark_auto_approval_failed.call_args.kwargs["review_remark"]
    repo.create_auto_result_notifications.assert_called_once()
    assert repo.create_auto_result_notifications.call_args.kwargs["status"] is WorkOrderStatus.FAILED
    repo.finalize_auto_approval.assert_not_called()

