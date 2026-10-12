"""Failure and result contracts for shared work-order orchestration."""

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.repository.implementations.work_orders.auto_approval import (
    _AutoApprovalWorkOrderRepository,
)
from agentclaw.community.core.work_orders.callbacks import (
    WorkOrderCallbackCredential,
    WorkOrderDecisionCallbackDispatcher,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAlreadyProcessedError,
    WorkOrderInvalidRemarkError,
    WorkOrderLocalFinalizeError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalMode,
    WorkOrderBizType,
    WorkOrderDecision,
    WorkOrderEventCreatedResult,
    WorkOrderEventStatus,
    WorkOrderEventType,
    WorkOrderStatus,
)
from agentclaw.community.core.work_orders.repository.models import WorkOrderModel
from agentclaw.community.core.work_orders.services.notification_service import (
    WorkOrderNotificationService,
)
from tests.community.core.work_orders.test_work_order_service import (
    _detail,
    _friend_context,
    _service,
)


@pytest.mark.parametrize("target", [WorkOrderStatus.APPROVED, WorkOrderStatus.REJECTED])
def test_bot_result_notice_has_applicant_and_decision(target):
    detail = _detail()
    detail.work_order.biz_type = WorkOrderBizType.BOT_COLLABORATOR.value
    detail.work_order.biz_id = "bot-1"
    draft = WorkOrderNotificationService.build_bot_editor_review_result(
        detail=detail, bot_name="Demo", target_status=target, review_remark="capacity"
    )
    assert draft.recipient_user_id == "applicant-1"
    assert draft.biz_id == "bot-1"
    assert draft.biz_type == WorkOrderBizType.BOT_COLLABORATOR
    assert draft.event_type == WorkOrderEventType.BOT_COLLABORATOR_REVIEWED
    assert draft.notification_category == NotificationCategory.NOTICE
    assert "Demo" in draft.content["text"]
    if target is WorkOrderStatus.REJECTED:
        assert "capacity" in draft.content["text"]
        assert "未通过" in draft.title
    else:
        assert "capacity" not in draft.content["text"]
        assert "已通过" in draft.title


@pytest.mark.parametrize("kind", ["space_join", "bot_editor"])
@pytest.mark.parametrize(
    "target", [WorkOrderStatus.REJECTED, WorkOrderStatus.PROCESSING]
)
def test_notice_builder_rejects_missing_rejection_reason_and_nonterminal_state(
    kind, target
):
    builder = getattr(WorkOrderNotificationService, f"build_{kind}_review_result")
    kwargs = {"bot_name": "Demo"} if kind == "bot_editor" else {}
    error = (
        WorkOrderInvalidRemarkError
        if target is WorkOrderStatus.REJECTED
        else ValueError
    )
    with pytest.raises(error):
        builder(detail=_detail(), target_status=target, review_remark=None, **kwargs)


@pytest.mark.parametrize(
    "callback_needed,conflict", [(True, False), (False, False), (True, True)]
)
def test_manual_persistence_failure_is_never_retried_or_reported_as_success(
    callback_needed, conflict
):
    callbacks = MagicMock(spec=WorkOrderDecisionCallbackDispatcher)
    callbacks.requires_callback.return_value = callback_needed
    service, repo, *_ = _service(decision_callbacks=callbacks)
    context = _friend_context()
    detail = _detail().model_copy(update={"work_order": context.work_order})
    service.get_detail = MagicMock(return_value=detail)
    repo.get_approval_context.return_value = context
    failure = (
        WorkOrderAlreadyProcessedError("conflict")
        if conflict
        else RuntimeError("write failed")
    )
    repo.process_approval.side_effect = failure
    expected = (
        WorkOrderLocalFinalizeError
        if callback_needed and not conflict
        else type(failure)
    )
    auth = WorkOrderCallbackCredential(headers={})

    with pytest.raises(expected) as caught:
        service.process_approval(
            work_order_id=11,
            actor_id="owner-1",
            decision=WorkOrderDecision.APPROVED,
            review_remark=None,
            callback_credential=auth,
        )

    if expected is WorkOrderLocalFinalizeError:
        assert caught.value.__cause__ is failure
        assert caught.value.work_order_id == 11
    else:
        assert caught.value is failure
    assert callbacks.dispatch.call_count == int(callback_needed)
    repo.process_approval.assert_called_once()
    repo.fail_auto_approval.assert_not_called()


def test_auto_bot_sync_runs_only_after_local_completion():
    service, repo, *_ = _service()
    repo.create_work_order_event.return_value = WorkOrderEventCreatedResult(
        event_category=NotificationCategory.APPROVAL,
        work_order_id=17,
        work_order_no="WO-17",
        notification_ids=[],
        status=WorkOrderEventStatus.PROCESSING,
    )
    events = []
    repo.complete_auto_approval.side_effect = lambda **kwargs: (
        events.append("commit") or [31]
    )
    service._collaborators.on_collaboration_changed.side_effect = lambda *args: (
        events.append("sync")
    )
    result = service.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        approval_mode=WorkOrderApprovalMode.AUTO,
        biz_type=WorkOrderBizType.BOT_COLLABORATOR.value,
        biz_id="bot-1",
        event_type=WorkOrderEventType.BOT_COLLABORATOR_APPLIED.value,
        applicant_user_id="actor",
        approver_user_ids=["recipient"],
        recipient_user_ids=[],
        title="AUTO request",
        content=None,
        apply_reason=None,
        biz_data={"bot_id": "bot-1", "owner_id": "owner-1"},
        actor_id="actor",
    )
    assert events == ["commit", "sync"]
    assert result.status is WorkOrderEventStatus.APPROVED
    assert result.notification_ids == [31]
    service._collaborators.on_collaboration_changed.assert_called_once_with(
        "bot-1", "owner-1", "dev"
    )


def test_auto_compare_and_set_conflict_never_creates_success_notice():
    repo = _AutoApprovalWorkOrderRepository()
    repo._WorkOrder = WorkOrderModel
    repo._insert_auto_result_notifications = MagicMock()
    session = MagicMock()
    session.query.return_value.filter.return_value.update.return_value = 0
    with pytest.raises(WorkOrderAlreadyProcessedError):
        repo._finish_auto_approval_in_session(
            session,
            order=WorkOrderModel(id=17),
            recipient_user_ids=["recipient"],
            source_event_type=WorkOrderEventType.BOT_COLLABORATOR_APPLIED.value,
            env="dev",
        )
    repo._insert_auto_result_notifications.assert_not_called()
    session.commit.assert_not_called()
