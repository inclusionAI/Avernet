"""AUTO mode orchestration tests for work-order service."""

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalMode,
    WorkOrderBizType,
    WorkOrderEventCreatedResult,
    WorkOrderEventStatus,
    WorkOrderEventType,
)
from agentclaw.community.core.work_orders.callbacks import (
    WorkOrderDecisionCallbackDispatcher,
)
from agentclaw.community.core.work_orders.errors import WorkOrderCallbackError
from agentclaw.community.core.work_orders.models import SYSTEM_REVIEWER_USER_ID
from tests.community.core.work_orders.test_work_order_service import _service


def _auto_request(service, biz_type, event_type, biz_id):
    return service.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        approval_mode=WorkOrderApprovalMode.AUTO,
        biz_type=biz_type,
        biz_id=biz_id,
        event_type=event_type,
        applicant_user_id="actor",
        approver_user_ids=[],
        recipient_user_ids=["notify-user"],
        title="AUTO request",
        content=None,
        apply_reason=None,
        biz_data={"request_ids": ["request-1"]}
        if biz_type == WorkOrderBizType.BOT_FRIEND.value
        else {},
        actor_id="actor",
    )


def test_auto_space_join_service_finalizes_and_notifies_after_callback():
    service, repo, *_ = _service()
    repo.complete_auto_approval.return_value = [31]
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
    repo.complete_auto_approval.assert_called_once_with(
        work_order_id=17,
        recipient_user_ids=["notify-user"],
        source_event_type=WorkOrderEventType.SPACE_JOIN_APPLIED.value,
        env="dev",
    )
    assert result.notification_ids == [31]


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
    repo.fail_auto_approval.return_value = [32]

    result = _auto_request(
        service,
        WorkOrderBizType.BOT_FRIEND.value,
        WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED.value,
        "friend-1",
    )

    assert result.status is WorkOrderEventStatus.FAILED
    repo.fail_auto_approval.assert_called_once()
    assert "callback broke" in repo.fail_auto_approval.call_args.kwargs["review_remark"]
    assert repo.fail_auto_approval.call_args.kwargs["recipient_user_ids"] == [
        "notify-user"
    ]
    repo.complete_auto_approval.assert_not_called()
    assert result.notification_ids == [32]


def test_successful_external_callback_is_not_later_reported_as_failed():
    callbacks = MagicMock(spec=WorkOrderDecisionCallbackDispatcher)
    callbacks.requires_callback.return_value = True
    service, repo, *_ = _service(decision_callbacks=callbacks)
    repo.create_work_order_event.return_value = WorkOrderEventCreatedResult(
        event_category=NotificationCategory.APPROVAL,
        work_order_id=21,
        work_order_no="WO-21",
        notification_ids=[],
        status=WorkOrderEventStatus.PENDING,
    )
    repo.get_approval_context.return_value.model_copy.return_value = object()
    repo.complete_auto_approval.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        _auto_request(
            service,
            WorkOrderBizType.BOT_FRIEND.value,
            WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED.value,
            "friend-1",
        )

    callbacks.dispatch.assert_called_once()
    repo.fail_auto_approval.assert_not_called()


def test_auto_skill_uses_atomic_work_order_repository_path():
    service, repo, *_ = _service()
    repo.apply_auto_skill_editor_request.return_value = [33]
    repo.create_work_order_event.return_value = WorkOrderEventCreatedResult(
        event_category=NotificationCategory.APPROVAL,
        work_order_id=19,
        work_order_no="WO-19",
        notification_ids=[],
        status=WorkOrderEventStatus.PENDING,
    )

    result = _auto_request(
        service,
        WorkOrderBizType.SKILL_COLLABORATOR.value,
        WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
        "skill-1",
    )

    assert result.status is WorkOrderEventStatus.APPROVED
    repo.apply_auto_skill_editor_request.assert_called_once_with(
        work_order_id=19,
        source_event_type=WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
        env="dev",
    )
    repo.complete_auto_approval.assert_not_called()
    repo.fail_auto_approval.assert_not_called()
    assert result.notification_ids == [33]
