"""Manager-sync notifications across Bot work-order approval paths."""
import json
from unittest.mock import MagicMock

import pytest

from tests.community.bot_address_fakes import support_legacy_locator
from tests.community.core.bot_collaborator.manager_sync.test_collaborator_triggers import RecordingNotifier
from tests.community.core.work_orders.test_work_order_service import NOW, _detail, _space, _work_order
from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import DisabledBotManagerSync
from agentclaw.community.core.bot_collaborator.services.collaborator_service import CollaboratorService
from agentclaw.community.core.work_orders.callbacks import (
    WorkOrderCallbackCredential, WorkOrderDecisionCallbackDispatcher,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory, WorkOrderApprovalMode, WorkOrderBizType,
    WorkOrderDecision, WorkOrderEventCreatedResult, WorkOrderEventStatus,
    WorkOrderEventType, WorkOrderNotificationDraft, WorkOrderRecord,
    WorkOrderReviewResult, WorkOrderStatus,
)
from agentclaw.community.core.work_orders.services.work_order_service import (
    WorkOrderNotificationService, WorkOrderService,
)
from agentclaw.community.kernel.bot_manager_sync import SyncKey, SyncReason
from agentclaw.community.plugin_api.staff_dept import StaffDeptPlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


# ── Manager-sync triggers through the three work-order paths (Task 7) ────────
#
# The work orders do not own a second notification path: creation auto-approve,
# manual approve and generic AUTO all converge on
# ``notify_bot_collaboration_changed`` → the real
# ``CollaboratorService.on_collaboration_changed``, whose notifier stays
# test-observable via the same RecordingNotifier the collaborator trigger
# tests use. Rejection / repository failure never notify; a post-commit
# notify failure keeps APPROVED and never re-submits the approval.


def _real_collaborators():
    """A real CollaboratorService whose notifier is a RecordingNotifier."""
    collaborator_repository = MagicMock()
    collaborator_repository.list_by_bot.return_value = []
    bot_repo = support_legacy_locator(MagicMock())
    bot_repo.get_by_id_and_owner.return_value = {
        "id": 17,
        "bot_id": "bot-17",
        "bot_name": "Editor Bot",
        "bot_type": "service",
        "owner_id": "owner-1",
        "space_id": None,
    }
    notifier = RecordingNotifier()
    service = CollaboratorService(
        collaborator_repo=collaborator_repository,
        bot_repo=bot_repo,
        passport_plugin=MagicMock(),
        credentials_admins_writer=MagicMock(),
        space_access_service=MagicMock(),
        manager_sync=notifier,
    )
    return service, notifier


def _work_order_service_with(collaborators):
    """WorkOrderService over mock plumbing and the given collaborators."""
    repository = MagicMock()
    spaces = MagicMock()
    access = MagicMock()
    notifications = MagicMock(spec=WorkOrderNotificationService)
    bots = MagicMock()
    collaborator_repository = MagicMock()
    collaborator_repository.get_by_bot_and_user.return_value = None
    member_management = MagicMock()
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    skill_handler = MagicMock()
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
        DisabledBotManagerSync(),
    )
    return service, repository, access, notifications, bots, member_management


def _bot_editor_work_order() -> WorkOrderRecord:
    return _work_order().model_copy(
        update={
            "biz_type": WorkOrderBizType.BOT_COLLABORATOR,
            "biz_id": "bot-17",
            "biz_data": json.dumps(
                {
                    "bot_id": "bot-17",
                    "bot_name": "Editor Bot",
                    "owner_id": "owner-1",
                }
            ),
        }
    )


def test_auto_approved_editor_request_creation_notifies_manager_sync() -> None:
    collaborators, notifier = _real_collaborators()
    (
        service,
        repository,
        access,
        _,
        bots,
        member_management,
    ) = _work_order_service_with(collaborators)
    bots.get_by_id_and_owner.return_value = {
        "id": 17,
        "bot_id": "bot-17",
        "bot_name": "Editor Bot",
        "bot_type": "service",
        "owner_id": "owner-1",
        "space_id": 7,
    }
    member_management.can_manage_collaborators.return_value = True
    access.require_space_reference.return_value = _space()
    record = _work_order().model_copy(update={"status": WorkOrderStatus.APPROVED})
    repository.create_bot_editor_request.return_value = record

    result = service.create_bot_editor_request(
        bot_id="bot-17",
        owner_id="owner-1",
        applicant_user_id="applicant-1",
        reason="joint editing",
    )

    assert result == record
    assert notifier.calls == [
        (
            SyncKey(
                avernet_tenant=get_current_avernet_tenant(), env="dev", bot_pk=17
            ),
            SyncReason.COLLABORATOR_CHANGED,
        )
    ]


def test_manual_editor_approval_notifies_manager_sync() -> None:
    collaborators, notifier = _real_collaborators()
    service, repository, _, notifications, _, _ = _work_order_service_with(
        collaborators
    )
    detail = _detail().model_copy(
        update={"work_order": _bot_editor_work_order()}
    )
    repository.get_detail.return_value = detail
    notification = WorkOrderNotificationDraft(
        recipient_user_id="applicant-1",
        notification_category=NotificationCategory.NOTICE,
        event_type=WorkOrderEventType.BOT_COLLABORATOR_REVIEWED,
        biz_type=WorkOrderBizType.BOT_COLLABORATOR,
        biz_id="bot-17",
        title="approved",
        content={"text": "approved"},
    )
    notifications.build_bot_editor_review_result.return_value = notification
    expected = WorkOrderReviewResult(
        work_order_id=11,
        status=WorkOrderStatus.APPROVED,
        decision=WorkOrderDecision.APPROVED,
        reviewer_user_id="owner-1",
        review_remark=None,
        reviewed_at=NOW,
    )
    repository.review_bot_editor_request.return_value = expected

    result = service.process_approval(
        work_order_id=11,
        actor_id="owner-1",
        decision=WorkOrderDecision.APPROVED,
        review_remark=None,
        callback_credential=WorkOrderCallbackCredential(headers={}),
    )

    assert result == expected
    assert notifier.calls == [
        (
            SyncKey(
                avernet_tenant=get_current_avernet_tenant(), env="dev", bot_pk=17
            ),
            SyncReason.COLLABORATOR_CHANGED,
        )
    ]


def test_generic_auto_approval_notifies_manager_sync() -> None:
    collaborators, notifier = _real_collaborators()
    service, repository, *_ = _work_order_service_with(collaborators)
    created = WorkOrderEventCreatedResult(
        event_category=NotificationCategory.APPROVAL,
        work_order_id=11,
        work_order_no="WO-11",
        notification_ids=[],
        status=WorkOrderEventStatus.PENDING,
    )
    repository.create_work_order_event.return_value = created
    repository.complete_auto_approval.return_value = [21]

    result = service.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        approval_mode=WorkOrderApprovalMode.AUTO,
        biz_type=WorkOrderBizType.BOT_COLLABORATOR.value,
        biz_id="bot-17",
        event_type=WorkOrderEventType.BOT_COLLABORATOR_APPLIED.value,
        applicant_user_id="applicant-1",
        approver_user_ids=[],
        recipient_user_ids=["owner-1"],
        title="pending",
        content=None,
        apply_reason=None,
        biz_data={"bot_id": "bot-17", "owner_id": "owner-1"},
        actor_id="applicant-1",
    )

    assert result.status == WorkOrderEventStatus.APPROVED
    assert notifier.calls == [
        (
            SyncKey(
                avernet_tenant=get_current_avernet_tenant(), env="dev", bot_pk=17
            ),
            SyncReason.COLLABORATOR_CHANGED,
        )
    ]


def test_editor_rejection_and_repository_failure_do_not_notify() -> None:
    collaborators, notifier = _real_collaborators()

    # Rejection: the review result is REJECTED, so the post-approval notify
    # never fires.
    service, repository, _, notifications, _, _ = _work_order_service_with(
        collaborators
    )
    repository.get_detail.return_value = _detail().model_copy(
        update={"work_order": _bot_editor_work_order()}
    )
    notifications.build_bot_editor_review_result.return_value = (
        WorkOrderNotificationDraft(
            recipient_user_id="applicant-1",
            notification_category=NotificationCategory.NOTICE,
            event_type=WorkOrderEventType.BOT_COLLABORATOR_REVIEWED,
            biz_type=WorkOrderBizType.BOT_COLLABORATOR,
            biz_id="bot-17",
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
    repository.review_bot_editor_request.return_value = rejected

    result = service.process_approval(
        work_order_id=11,
        actor_id="owner-1",
        decision=WorkOrderDecision.REJECTED,
        review_remark="no",
        callback_credential=WorkOrderCallbackCredential(headers={}),
    )

    assert result == rejected
    assert notifier.calls == []

    # Repository failure: the review write raises before any notify.
    collaborators_retry, retry_notifier = _real_collaborators()
    service, repository, _, _, _, _ = _work_order_service_with(collaborators_retry)
    repository.get_detail.return_value = _detail().model_copy(
        update={"work_order": _bot_editor_work_order()}
    )
    repository.review_bot_editor_request.side_effect = RuntimeError(
        "repository unavailable"
    )

    with pytest.raises(RuntimeError, match="repository unavailable"):
        service.process_approval(
            work_order_id=11,
            actor_id="owner-1",
            decision=WorkOrderDecision.APPROVED,
            review_remark=None,
            callback_credential=WorkOrderCallbackCredential(headers={}),
        )
    assert retry_notifier.calls == []


def test_post_commit_notify_failure_keeps_approval_and_never_rereviews() -> None:
    """A failed post-commit projection must not change the approval result.

    The roster read inside the real callback raises after the notifier has
    admitted the sync; ``notify_bot_collaboration_changed`` swallows the
    failure, the approval stays APPROVED, and the approval write is never
    re-submitted.
    """
    collaborators, notifier = _real_collaborators()
    # The notifier fires before the roster read, so make the roster read the
    # exploding leg of the post-commit projection.
    collaborators._collaborator_repo.list_by_bot.side_effect = RuntimeError(
        "roster unavailable"
    )
    service, repository, _, notifications, _, _ = _work_order_service_with(
        collaborators
    )
    repository.get_detail.return_value = _detail().model_copy(
        update={"work_order": _bot_editor_work_order()}
    )
    notifications.build_bot_editor_review_result.return_value = (
        WorkOrderNotificationDraft(
            recipient_user_id="applicant-1",
            notification_category=NotificationCategory.NOTICE,
            event_type=WorkOrderEventType.BOT_COLLABORATOR_REVIEWED,
            biz_type=WorkOrderBizType.BOT_COLLABORATOR,
            biz_id="bot-17",
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
    repository.review_bot_editor_request.return_value = expected

    result = service.process_approval(
        work_order_id=11,
        actor_id="owner-1",
        decision=WorkOrderDecision.APPROVED,
        review_remark=None,
        callback_credential=WorkOrderCallbackCredential(headers={}),
    )

    assert result == expected
    assert result.status == WorkOrderStatus.APPROVED
    assert notifier.calls == [
        (
            SyncKey(
                avernet_tenant=get_current_avernet_tenant(), env="dev", bot_pk=17
            ),
            SyncReason.COLLABORATOR_CHANGED,
        )
    ]
    repository.review_bot_editor_request.assert_called_once()
