"""Space-join work orders reach the manager-sync side-channel (Task 9).

Manual review and generic AUTO completion of a SPACE_JOIN work order
both land the applicant in the Space, so both submit the same
best-effort hint the Space flows already use
(``notify_space_membership_changed`` → ``BotManagerSyncProtocol.request_space_sync``):

- The hint carries only small refs — the full triple the permission
  check already resolved for manual review, the validated numeric refs
  for AUTO (the background resolver extends them while the Space lives)
  — plus the applicant's user id. It replaces any collaborator
  re-insertion for membership-restoration cases: Space eligibility of
  stale collaborator rows is the projector's business, reached via the
  fan-out.
- REJECTED / FAILED terminal states, repository-commit failures and
  finalize exceptions stay silent; a failed submit never changes the
  business result.
- The three BOT_COLLABORATOR paths (Task 7) keep their own one-notify
  contract in ``test_work_order_service.py``; this file pins the
  SPACE_JOIN legs only.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, Mock

import pytest

from tests.community.core.bot_collaborator.manager_sync.test_indirect_triggers import (
    RecordingNotifier,
)

from agentclaw.community.core.spaces.models import SpaceRecord, SpaceType
from agentclaw.community.core.spaces.services.space_access_service import (
    SpaceAccessService,
)
from agentclaw.community.core.work_orders.callbacks import (
    WorkOrderCallbackCredential,
    WorkOrderDecisionCallbackDispatcher,
)
from agentclaw.community.core.work_orders.errors import WorkOrderLocalFinalizeError
from agentclaw.community.core.work_orders.execution import (
    notify_space_membership_changed,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalContext,
    WorkOrderApprovalMode,
    WorkOrderApproverRecord,
    WorkOrderApproverStatus,
    WorkOrderBizType,
    WorkOrderDecision,
    WorkOrderDetail,
    WorkOrderEventCreatedResult,
    WorkOrderEventStatus,
    WorkOrderEventType,
    WorkOrderNotificationDraft,
    WorkOrderRecord,
    WorkOrderReviewResult,
    WorkOrderStatus,
)
from agentclaw.community.core.work_orders.services.work_order_service import (
    WorkOrderService,
)
from agentclaw.community.kernel.bot_manager_sync import SpaceChange
from agentclaw.community.plugin_api.staff_dept import (
    StaffDeptPlugin,
    StaffProfileInfo,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

NOW = datetime(2026, 10, 27, 10, 0, 0)

#: Outside any request scope every test runs on the default tenant.
TENANT = get_current_avernet_tenant()

#: ``get_current_env()``'s answer in the unit column.
ENV = "dev"


def test_postcommit_space_notification_does_not_raise():
    sync = Mock()
    sync.request_space_sync.side_effect = RuntimeError("queue full")
    change = SpaceChange("tenant-a", "dev", ("22", "team:22"), ("member",))
    assert notify_space_membership_changed(sync, change=change) is None
    sync.request_space_sync.assert_called_once_with(change)


def _space():
    return SpaceRecord(
        id=7,
        space_code="spc-7",
        space_type=SpaceType.TEAM,
        name="Team",
        personal_owner_id=None,
        env=ENV,
        created_by="owner-1",
        updated_by="owner-1",
        gmt_created=NOW,
        gmt_modified=NOW,
    )


def _work_order() -> WorkOrderRecord:
    return WorkOrderRecord(
        id=11,
        work_order_no="WO-11",
        biz_type=WorkOrderBizType.SPACE_JOIN,
        biz_id="7",
        applicant_user_id="applicant-1",
        apply_reason="join",
        status=WorkOrderStatus.PENDING,
        reviewer_user_id=None,
        review_remark=None,
        reviewed_at=None,
        env=ENV,
        gmt_created=NOW,
        gmt_modified=NOW,
    )


def _detail() -> WorkOrderDetail:
    return WorkOrderDetail(
        work_order=_work_order(),
        event_type=WorkOrderEventType.SPACE_JOIN_APPLIED,
        title="pending",
        space_id=7,
        space_name="Team",
        applicant_name="Applicant",
        can_approve=True,
    )


def _auto_context() -> WorkOrderApprovalContext:
    return WorkOrderApprovalContext(
        work_order=_work_order(),
        approver=WorkOrderApproverRecord(
            id=31,
            work_order_id=11,
            approver_user_id="owner-1",
            status=WorkOrderApproverStatus.PENDING,
            review_remark=None,
            reviewed_at=None,
            env=ENV,
            gmt_created=NOW,
            gmt_modified=NOW,
        ),
        source_event_type=WorkOrderEventType.SPACE_JOIN_APPLIED.value,
    )


def _service_with(sync, *, decision_callbacks=None):
    """The mocked work-order world with a real sync notifier double.

    Mirrors the Task 7 fixture in ``test_work_order_service``: the owner
    permission check returns the resolved Space so the refs come from it,
    and the (mocked) repository stands in for the transaction that
    re-activates the applicant's membership inside ``review_space_join``.
    """
    repository = MagicMock()
    spaces = MagicMock()
    access = MagicMock(spec=SpaceAccessService)
    access.require_space_owner.return_value = (_space(), MagicMock())
    notifications = MagicMock()
    bots = MagicMock()
    collaborator_repository = MagicMock()
    # A collaborator row still exists for the applicant from before they
    # left the Space (eligible=False until the Space vouches again).
    collaborator_repository.get_by_bot_and_user.return_value = MagicMock(
        user_id="applicant-1", space_eligible=False
    )
    collaborators = MagicMock()
    member_management = MagicMock()
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="applicant-1",
        nick_name=None,
    )
    skill_handler = MagicMock()
    if decision_callbacks is None:
        decision_callbacks = MagicMock(spec=WorkOrderDecisionCallbackDispatcher)
        decision_callbacks.requires_callback.return_value = False
    service = WorkOrderService(
        repository,
        spaces,
        access,
        notifications,
        bots,
        collaborator_repository,
        collaborators,
        member_management,
        staff_dept,
        skill_handler,
        decision_callbacks,
        sync,
    )
    return (
        service,
        repository,
        access,
        notifications,
        collaborators,
        collaborator_repository,
    )


def _manual_review_case(notifier):
    service, repository, access, notifications, collaborators, _ = _service_with(
        notifier
    )
    repository.get_detail.return_value = _detail()
    notifications.build_space_join_review_result.return_value = (
        WorkOrderNotificationDraft(
            recipient_user_id="applicant-1",
            notification_category=NotificationCategory.NOTICE,
            event_type=WorkOrderEventType.SPACE_JOIN_REVIEWED,
            biz_type=WorkOrderBizType.SPACE_JOIN,
            biz_id="7",
            title="approved",
            content={"text": "approved"},
        )
    )
    expected = WorkOrderReviewResult(
        work_order_id=11,
        status=WorkOrderStatus.APPROVED,
        decision=WorkOrderDecision.APPROVED,
        reviewer_user_id="owner-1",
        review_remark=None,
        reviewed_at=NOW,
    )
    repository.review_space_join.return_value = expected
    return service, repository, notifications, collaborators, expected


def test_manual_space_join_approval_submits_space_hint_without_collaborator_insert():
    """Re-joining restores membership, and the hint replaces re-insertion."""
    notifier = RecordingNotifier()
    service, repository, _, collaborators, expected = _manual_review_case(notifier)

    result = service.approve(work_order_id=11, actor_id="owner-1", review_remark=None)

    assert result == expected
    assert notifier.changes == [
        SpaceChange(
            avernet_tenant=TENANT,
            env=ENV,
            space_refs=("7", "team:7", "spc-7"),
            user_ids=("applicant-1",),
        )
    ]
    # The membership write stays inside the review transaction; no second
    # collaborator insert and no Bot-level notification happen here.
    repository.review_space_join.assert_called_once()
    collaborators.on_collaboration_changed.assert_not_called()


def test_manual_space_join_notify_failure_keeps_approval():
    notifier = RecordingNotifier()
    service, _, _, _, expected = _manual_review_case(notifier)
    notifier.request_space_sync.side_effect = RuntimeError("queue full")

    result = service.approve(work_order_id=11, actor_id="owner-1", review_remark=None)

    assert result == expected
    assert result.status is WorkOrderStatus.APPROVED
    notifier.request_space_sync.assert_called_once()


def test_manual_space_join_rejection_stays_silent():
    notifier = RecordingNotifier()
    service, repository, notifications, _, _ = _manual_review_case(notifier)
    rejected_detail = _detail()
    repository.get_detail.return_value = rejected_detail
    notifications.build_space_join_review_result.return_value = (
        WorkOrderNotificationDraft(
            recipient_user_id="applicant-1",
            notification_category=NotificationCategory.NOTICE,
            event_type=WorkOrderEventType.SPACE_JOIN_REVIEWED,
            biz_type=WorkOrderBizType.SPACE_JOIN,
            biz_id="7",
            title="rejected",
            content={"text": "rejected"},
        )
    )
    rejected = WorkOrderReviewResult(
        work_order_id=11,
        status=WorkOrderStatus.REJECTED,
        decision=WorkOrderDecision.REJECTED,
        reviewer_user_id="owner-1",
        review_remark="no",
        reviewed_at=NOW,
    )
    repository.review_space_join.return_value = rejected

    result = service.reject(work_order_id=11, actor_id="owner-1", review_remark="  no  ")

    assert result == rejected
    assert notifier.changes == []


def test_manual_space_join_review_failure_propagates_and_stays_silent():
    notifier = RecordingNotifier()
    service, repository, _, _, _ = _manual_review_case(notifier)
    repository.review_space_join.side_effect = RuntimeError("commit unavailable")

    with pytest.raises(RuntimeError, match="commit unavailable"):
        service.approve(work_order_id=11, actor_id="owner-1", review_remark=None)

    assert notifier.changes == []


def _auto_join_case(notifier, *, decision_callbacks=None):
    service, repository, access, notifications, collaborators, _ = _service_with(
        notifier, decision_callbacks=decision_callbacks
    )
    created = WorkOrderEventCreatedResult(
        event_category=NotificationCategory.APPROVAL,
        work_order_id=11,
        work_order_no="WO-11",
        notification_ids=[],
        status=WorkOrderEventStatus.PENDING,
    )
    repository.create_work_order_event.return_value = created
    repository.get_approval_context.return_value = _auto_context()
    return service, repository, notifications, collaborators


def _submit_auto_space_join(service):
    return service.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        approval_mode=WorkOrderApprovalMode.AUTO,
        biz_type=WorkOrderBizType.SPACE_JOIN.value,
        biz_id="7",
        event_type=WorkOrderEventType.SPACE_JOIN_APPLIED.value,
        applicant_user_id="applicant-1",
        approver_user_ids=[],
        recipient_user_ids=["owner-1"],
        title="pending",
        content=None,
        apply_reason=None,
        biz_data=None,
        actor_id="applicant-1",
    )


def test_auto_space_join_completion_submits_minimal_refs_hint():
    """AUTO completes with the validated Space id; no Bot-level callback."""
    notifier = RecordingNotifier()
    service, repository, _, collaborators = _auto_join_case(notifier)
    repository.complete_auto_approval.return_value = [21]

    result = _submit_auto_space_join(service)

    assert result.status is WorkOrderEventStatus.APPROVED
    assert result.notification_ids == [21]
    assert notifier.changes == [
        SpaceChange(
            avernet_tenant=TENANT,
            env=ENV,
            space_refs=("7", "team:7"),
            user_ids=("applicant-1",),
        )
    ]
    # The local context has no space_code: the background resolver extends
    # the numeric refs while the Space exists, so no extra read runs here.
    repository.complete_auto_approval.assert_called_once_with(
        work_order_id=11,
        recipient_user_ids=["owner-1"],
        source_event_type=WorkOrderEventType.SPACE_JOIN_APPLIED.value,
        env=ENV,
    )
    collaborators.on_collaboration_changed.assert_not_called()


def test_auto_space_join_notify_failure_keeps_approved():
    notifier = RecordingNotifier()
    service, repository, _, _ = _auto_join_case(notifier)
    repository.complete_auto_approval.return_value = [21]
    notifier.request_space_sync.side_effect = RuntimeError("queue full")

    result = _submit_auto_space_join(service)

    assert result.status is WorkOrderEventStatus.APPROVED
    assert result.notification_ids == [21]
    notifier.request_space_sync.assert_called_once()


def test_auto_space_join_failure_terminal_state_stays_silent():
    notifier = RecordingNotifier()
    service, repository, _, _ = _auto_join_case(notifier)
    repository.complete_auto_approval.side_effect = RuntimeError("join conflict")
    repository.fail_auto_approval.return_value = [31]

    result = _submit_auto_space_join(service)

    assert result.status is WorkOrderEventStatus.FAILED
    assert result.notification_ids == [31]
    assert notifier.changes == []


def test_space_join_finalize_failure_does_not_notify():
    """A repository-commit failure at finalize propagates; no hint follows.

    The BCN callback already confirmed the decision (callback succeeded),
    so the local finalize error must not be diluted into a FAILED flow:
    the original failure propagates as ``WorkOrderLocalFinalizeError``
    (cause preserved) and the notifier records zero calls.
    """
    notifier = RecordingNotifier()
    callbacks = MagicMock(spec=WorkOrderDecisionCallbackDispatcher)
    callbacks.requires_callback.return_value = True
    service, repository, _, _ = _auto_join_case(notifier, decision_callbacks=callbacks)
    repository.complete_auto_approval.side_effect = RuntimeError("commit failed")

    with pytest.raises(WorkOrderLocalFinalizeError) as exc_info:
        _submit_auto_space_join(service)

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert notifier.changes == []
    repository.fail_auto_approval.assert_not_called()


def test_manual_approval_via_process_approval_path_also_hints():
    """The unified review entrypoint funnels into the same post-commit hint."""
    notifier = RecordingNotifier()
    service, repository, notifications, collaborators, expected = _manual_review_case(
        notifier
    )
    del notifications, collaborators

    result = service.process_approval(
        work_order_id=11,
        actor_id="owner-1",
        decision=WorkOrderDecision.APPROVED,
        review_remark=None,
        callback_credential=WorkOrderCallbackCredential(headers={}),
    )

    assert result == expected
    assert notifier.changes == [
        SpaceChange(
            avernet_tenant=TENANT,
            env=ENV,
            space_refs=("7", "team:7", "spc-7"),
            user_ids=("applicant-1",),
        )
    ]
    repository.review_space_join.assert_called_once()