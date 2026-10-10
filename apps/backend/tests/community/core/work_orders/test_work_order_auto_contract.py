"""AUTO recipient contract, callback ordering, and failure isolation."""

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.work_orders.callbacks import (
    WorkOrderDecisionCallbackDispatcher,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAlreadyProcessedError,
    WorkOrderInvalidEventError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalMode,
    WorkOrderEventCreatedResult,
    WorkOrderEventStatus,
)
from tests.community.core.work_orders.test_work_order_service import _service


def _create(service, *, approvers, recipients, skill=False):
    return service.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        approval_mode=WorkOrderApprovalMode.AUTO,
        biz_type="SKILL_COLLABORATOR" if skill else "BOT_FRIEND",
        biz_id="resource-1",
        event_type="SKILL_COLLABORATOR_APPLIED"
        if skill
        else "HUMAN2BOT_FRIEND_APPLIED",
        applicant_user_id="actor",
        approver_user_ids=approvers,
        recipient_user_ids=recipients,
        title="AUTO request",
        content=None,
        apply_reason=None,
        biz_data={} if skill else {"request_ids": ["request-1"]},
        actor_id="actor",
    )


def _configured():
    callbacks = MagicMock(spec=WorkOrderDecisionCallbackDispatcher)
    callbacks.requires_callback.return_value = True
    service, repo, *_ = _service(decision_callbacks=callbacks)
    repo.create_work_order_event.return_value = WorkOrderEventCreatedResult(
        event_category=NotificationCategory.APPROVAL,
        work_order_id=17,
        work_order_no="WO-17",
        notification_ids=[],
        status=WorkOrderEventStatus.PROCESSING,
    )
    repo.complete_auto_approval.return_value = [31, 32]
    repo.apply_auto_skill_editor_request.return_value = [31, 32]
    repo.fail_auto_approval.return_value = [33, 34]
    return service, repo, callbacks


@pytest.mark.parametrize("approvers", [[], [""], [" ", "\t"]])
@pytest.mark.parametrize("recipients", [[], ["cannot-be-fallback"]])
def test_auto_requires_nonblank_approvers_before_any_write(approvers, recipients):
    service, repo, callbacks = _configured()
    with pytest.raises(WorkOrderInvalidEventError, match="approver_user_ids"):
        _create(service, approvers=approvers, recipients=recipients)
    repo.create_work_order_event.assert_not_called()
    callbacks.dispatch.assert_not_called()


@pytest.mark.parametrize("skill", [False, True])
@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("recipients", [[], ["unrelated-recipient"]])
def test_auto_uses_only_normalized_approvers_for_all_results(skill, fails, recipients):
    service, repo, callbacks = _configured()
    completion = (
        repo.apply_auto_skill_editor_request if skill else repo.complete_auto_approval
    )
    if fails:
        if skill:
            completion.side_effect = RuntimeError("sensitive external response")
        else:
            callbacks.dispatch.side_effect = RuntimeError("sensitive external response")
    result = _create(
        service,
        approvers=[" first ", "", "second", "first", "  "],
        recipients=recipients,
        skill=skill,
    )
    expected = ["first", "second"]
    created = repo.create_work_order_event.call_args.kwargs
    assert created["approver_user_ids"] == expected
    assert created["recipient_user_ids"] == expected
    terminal = repo.fail_auto_approval if fails else completion
    assert terminal.call_args.kwargs["recipient_user_ids"] == expected
    assert result.status is (
        WorkOrderEventStatus.FAILED if fails else WorkOrderEventStatus.APPROVED
    )
    assert result.notification_ids == ([33, 34] if fails else [31, 32])
    repo.claim_auto_approval.assert_not_called()
    if fails:
        assert (
            "sensitive external response"
            not in terminal.call_args.kwargs["review_remark"]
        )
        assert len(terminal.call_args.kwargs["review_remark"]) <= 512
    if skill:
        callbacks.dispatch.assert_not_called()
        repo.complete_auto_approval.assert_not_called()


def test_friend_callback_finishes_before_local_completion():
    service, repo, callbacks = _configured()
    order = []
    callbacks.dispatch.side_effect = lambda **kwargs: order.append("callback")

    def complete(**kwargs):
        order.append("complete")
        return [31]

    repo.complete_auto_approval.side_effect = complete
    _create(service, approvers=["recipient"], recipients=[])
    assert order == ["callback", "complete"]
    callbacks.dispatch.assert_called_once()


def test_friend_without_registered_callback_cannot_complete():
    service, repo, callbacks = _configured()
    callbacks.requires_callback.return_value = False
    result = _create(service, approvers=["recipient"], recipients=[])
    assert result.status is WorkOrderEventStatus.FAILED
    repo.complete_auto_approval.assert_not_called()
    callbacks.dispatch.assert_not_called()


@pytest.mark.parametrize("skill", [False, True])
def test_already_processed_never_attempts_failure_transition(skill):
    service, repo, callbacks = _configured()
    failure = WorkOrderAlreadyProcessedError("already completed")
    if skill:
        repo.apply_auto_skill_editor_request.side_effect = failure
    else:
        repo.get_approval_context.side_effect = failure
    with pytest.raises(WorkOrderAlreadyProcessedError) as caught:
        _create(service, approvers=["recipient"], recipients=[], skill=skill)
    assert caught.value is failure
    repo.fail_auto_approval.assert_not_called()
    callbacks.dispatch.assert_not_called()


def test_failure_persistence_error_is_not_hidden():
    service, repo, callbacks = _configured()
    callbacks.dispatch.side_effect = RuntimeError("business failed")
    failure = RuntimeError("failure recording unavailable")
    repo.fail_auto_approval.side_effect = failure
    with pytest.raises(RuntimeError, match="failure recording unavailable") as caught:
        _create(service, approvers=["recipient"], recipients=[])
    assert caught.value is failure
    callbacks.dispatch.assert_called_once()
    repo.complete_auto_approval.assert_not_called()
