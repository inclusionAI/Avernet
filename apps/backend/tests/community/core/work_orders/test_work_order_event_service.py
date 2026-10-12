"""Work-order event and business regression cases split by responsibility."""

from unittest.mock import MagicMock, call
import pytest
from agentclaw.community.core.work_orders.callbacks import (
    WorkOrderCallbackCredential,
    WorkOrderDecisionCallbackDispatcher,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyProcessedError,
    WorkOrderCallbackError,
    WorkOrderInvalidEventError,
    WorkOrderNotificationNotFoundError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalMode,
    WorkOrderApproverStatus,
    WorkOrderBizType,
    WorkOrderDecision,
    APPROVAL_EVENT_TYPES,
    EVENT_CATEGORIES,
    WorkOrderEventType,
    WorkOrderEventStatus,
    WorkOrderEventCreatedResult,
    WorkOrderNotificationDetail,
    WorkOrderNotificationDraft,
    WorkOrderNotificationBadgeSummary,
    WorkOrderReviewResult,
    WorkOrderStatus,
    WorkOrderTitleKey,
)
from agentclaw.community.core.work_orders.services.work_order_service import (
    WorkOrderNotificationService,
)

from tests.community.core.work_orders.test_work_order_service import (
    NOW,
    _detail,
    _friend_context,
    _notification,
    _service,
)


def test_friend_approval_calls_callback_before_local_persistence() -> None:
    callbacks = MagicMock(spec=WorkOrderDecisionCallbackDispatcher)
    callbacks.requires_callback.return_value = True
    service, repository, _, _, _ = _service(decision_callbacks=callbacks)
    context = _friend_context()
    repository.get_detail.return_value = _detail().model_copy(
        update={"work_order": context.work_order}
    )
    repository.get_approval_context.return_value = context
    expected = WorkOrderReviewResult(
        work_order_id=11,
        status=WorkOrderStatus.APPROVED,
        decision=WorkOrderDecision.APPROVED,
        reviewer_user_id="owner-1",
        review_remark=None,
        reviewed_at=NOW,
    )
    repository.process_approval.return_value = expected
    calls = MagicMock()
    calls.attach_mock(callbacks, "callbacks")
    calls.attach_mock(repository, "repository")
    credential = WorkOrderCallbackCredential(headers={"Authorization": "Bearer token"})

    result = service.process_approval(
        work_order_id=11,
        actor_id="owner-1",
        decision=WorkOrderDecision.APPROVED,
        review_remark=None,
        callback_credential=credential,
    )

    assert result == expected
    callbacks.dispatch.assert_called_once_with(
        context=context,
        decision=WorkOrderDecision.APPROVED,
        review_remark=None,
        credential=credential,
    )
    assert calls.mock_calls.index(
        call.callbacks.dispatch(
            context=context,
            decision=WorkOrderDecision.APPROVED,
            review_remark=None,
            credential=credential,
        )
    ) < calls.mock_calls.index(
        call.repository.process_approval(
            work_order_id=11,
            reviewer_user_id="owner-1",
            decision=WorkOrderDecision.APPROVED,
            review_remark=None,
            env="dev",
        )
    )


def test_friend_callback_failure_keeps_local_approval_pending() -> None:
    callbacks = MagicMock(spec=WorkOrderDecisionCallbackDispatcher)
    callbacks.requires_callback.return_value = True
    callbacks.dispatch.side_effect = WorkOrderCallbackError("BCN failed")
    service, repository, _, _, _ = _service(decision_callbacks=callbacks)
    context = _friend_context()
    repository.get_detail.return_value = _detail().model_copy(
        update={"work_order": context.work_order}
    )
    repository.get_approval_context.return_value = context

    with pytest.raises(WorkOrderCallbackError):
        service.process_approval(
            work_order_id=11,
            actor_id="owner-1",
            decision=WorkOrderDecision.REJECTED,
            review_remark="no",
            callback_credential=WorkOrderCallbackCredential(headers={}),
        )

    repository.process_approval.assert_not_called()


@pytest.mark.parametrize(
    ("order_status", "approver_status"),
    [
        (WorkOrderStatus.APPROVED, WorkOrderApproverStatus.APPROVED),
        (WorkOrderStatus.PENDING, WorkOrderApproverStatus.CANCELLED),
    ],
)
def test_friend_approval_does_not_repeat_callback_after_processing(
    order_status: WorkOrderStatus,
    approver_status: WorkOrderApproverStatus,
) -> None:
    callbacks = MagicMock(spec=WorkOrderDecisionCallbackDispatcher)
    callbacks.requires_callback.return_value = True
    service, repository, _, _, _ = _service(decision_callbacks=callbacks)
    context = _friend_context(
        order_status=order_status,
        approver_status=approver_status,
    )
    repository.get_detail.return_value = _detail().model_copy(
        update={"work_order": context.work_order}
    )
    repository.get_approval_context.return_value = context

    with pytest.raises(WorkOrderAlreadyProcessedError):
        service.process_approval(
            work_order_id=11,
            actor_id="owner-1",
            decision=WorkOrderDecision.APPROVED,
            review_remark=None,
            callback_credential=WorkOrderCallbackCredential(headers={}),
        )

    callbacks.dispatch.assert_not_called()
    repository.process_approval.assert_not_called()


@pytest.mark.parametrize(
    "biz_data",
    [None, {}, {"request_ids": []}, {"request_ids": [""]}, {"request_ids": [7]}],
)
def test_create_friend_event_requires_callback_contract(
    biz_data: dict[str, object] | None,
) -> None:
    service, repository, _, _, _ = _service()

    with pytest.raises(WorkOrderInvalidEventError):
        service.create_work_order_event(
            event_category=NotificationCategory.APPROVAL,
            biz_type=WorkOrderBizType.BOT_FRIEND.value,
            biz_id="friend-request",
            event_type=WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED.value,
            applicant_user_id="actor-1",
            approver_user_ids=["approver-1"],
            recipient_user_ids=[],
            title="friend request",
            content=None,
            apply_reason=None,
            biz_data=biz_data,
            actor_id="actor-1",
        )

    repository.create_work_order_event.assert_not_called()


def test_auto_work_order_rejects_applicant_other_than_actor() -> None:
    service, repository, _, _, _ = _service()

    with pytest.raises(WorkOrderAccessDeniedError, match="applicant must be"):
        service.create_work_order_event(
            event_category=NotificationCategory.APPROVAL,
            approval_mode=WorkOrderApprovalMode.AUTO,
            biz_type=WorkOrderBizType.BOT_FRIEND.value,
            biz_id="friend-auto",
            event_type=WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED.value,
            applicant_user_id="someone-else",
            approver_user_ids=['result-recipient'],
            recipient_user_ids=[],
            title="friend request",
            content=None,
            apply_reason=None,
            biz_data={"request_ids": ["request-auto"]},
            actor_id="actor-auto",
        )

    repository.create_work_order_event.assert_not_called()


def test_auto_friend_event_defers_callback_to_repository_with_real_context() -> None:
    callbacks = MagicMock(spec=WorkOrderDecisionCallbackDispatcher)
    callbacks.requires_callback.return_value = True
    service, repository, _, _, _ = _service(decision_callbacks=callbacks)
    repository.create_work_order_event.return_value = WorkOrderEventCreatedResult(
        event_category=NotificationCategory.APPROVAL,
        work_order_id=11,
        work_order_no="WO-11",
        notification_ids=[21],
        status=WorkOrderEventStatus.APPROVED,
    )
    callback_context = WorkOrderCallbackCredential(headers={"X-Request-Id": "req-1"})

    result = service.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        approval_mode=WorkOrderApprovalMode.AUTO,
        biz_type=WorkOrderBizType.BOT_FRIEND.value,
        biz_id="friend-auto",
        event_type=WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED.value,
        applicant_user_id="actor-auto",
        approver_user_ids=['result-recipient'],
        recipient_user_ids=[],
        title="friend request",
        content=None,
        apply_reason=None,
        biz_data={"request_ids": ["request-auto"]},
        actor_id="actor-auto",
        callback_auth=callback_context,
    )

    assert result.status is WorkOrderEventStatus.APPROVED
    kwargs = repository.create_work_order_event.call_args.kwargs
    assert kwargs["approver_user_ids"] == ["result-recipient"]
    assert kwargs["recipient_user_ids"] == ["result-recipient"]
    assert kwargs["callback_source_event_type"] == (
        WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED.value
    )
    callbacks.dispatch.assert_called_once()
    dispatch_kwargs = callbacks.dispatch.call_args.kwargs
    repository.get_approval_context.return_value.model_copy.assert_called_once_with(
        update={"source_event_type": WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED.value}
    )
    assert dispatch_kwargs["decision"] is WorkOrderDecision.APPROVED
    assert dispatch_kwargs["review_remark"] is None
    assert dispatch_kwargs["credential"] is callback_context


def test_auto_mode_is_rejected_for_notice_events() -> None:
    service, repository, _, _, _ = _service()

    with pytest.raises(WorkOrderInvalidEventError, match="only valid for approval"):
        service.create_work_order_event(
            event_category=NotificationCategory.NOTICE,
            approval_mode=WorkOrderApprovalMode.AUTO,
            biz_type=WorkOrderBizType.GROUP_MENTION.value,
            biz_id="group-1:s1",
            event_type=WorkOrderEventType.HUMAN_GROUP_MENTIONED.value,
            applicant_user_id=None,
            approver_user_ids=['recipient-1'],
            recipient_user_ids=[],
            title="你被 @ 了",
            content={"text": "hello"},
            apply_reason=None,
            biz_data=None,
            actor_id="actor-1",
        )

    repository.create_work_order_event.assert_not_called()


def test_create_group_mention_notice_event_reaches_repository() -> None:
    service, repository, _, _, _ = _service()

    service.create_work_order_event(
        event_category=NotificationCategory.NOTICE,
        biz_type=WorkOrderBizType.GROUP_MENTION.value,
        biz_id="group-1:s1",
        event_type=WorkOrderEventType.HUMAN_GROUP_MENTIONED.value,
        applicant_user_id=None,
        approver_user_ids=[],
        recipient_user_ids=["447147"],
        title="你被 @ 了",
        content={"text": "张三: 你好"},
        apply_reason=None,
        biz_data={
            "group_id": "group-1",
            "session_id": "group-1:s1",
            "sender_actor_id": "bot-driver",
        },
        actor_id="447147",
    )

    call = repository.create_work_order_event.call_args.kwargs
    assert call["event_type"] == WorkOrderEventType.HUMAN_GROUP_MENTIONED.value
    assert call["biz_type"] == WorkOrderBizType.GROUP_MENTION.value
    assert call["recipient_user_ids"] == ["447147"]


@pytest.mark.parametrize(
    ("status", "expected_title", "expected_content"),
    [
        (
            WorkOrderStatus.APPROVED,
            WorkOrderTitleKey.SPACE_JOIN_APPROVED.value,
            "你加入空间「Team」的申请已通过。",
        ),
        (
            WorkOrderStatus.REJECTED,
            WorkOrderTitleKey.SPACE_JOIN_REJECTED.value,
            "你加入空间「Team」的申请未通过。拒绝原因：capacity",
        ),
    ],
)
def test_notification_service_builds_space_join_review_result(
    status: WorkOrderStatus, expected_title: str, expected_content: str
) -> None:
    draft = WorkOrderNotificationService.build_space_join_review_result(
        detail=_detail(),
        target_status=status,
        review_remark="capacity",
    )

    assert draft == WorkOrderNotificationDraft(
        recipient_user_id="applicant-1",
        notification_category=NotificationCategory.NOTICE,
        event_type=WorkOrderEventType.SPACE_JOIN_REVIEWED,
        biz_type=WorkOrderBizType.SPACE_JOIN,
        biz_id="7",
        title=expected_title,
        content={"text": expected_content},
    )


def test_notification_service_rejects_unsupported_review_status() -> None:
    with pytest.raises(ValueError, match="unsupported review status: PENDING"):
        WorkOrderNotificationService.build_space_join_review_result(
            detail=_detail(),
            target_status=WorkOrderStatus.PENDING,
            review_remark="pending",
        )


def test_notification_service_delegates_and_maps_missing_records() -> None:
    repository = MagicMock()
    service = WorkOrderNotificationService(repository)
    detail = WorkOrderNotificationDetail(
        notification=_notification(),
        work_order_status=WorkOrderStatus.PENDING,
        can_approve=True,
    )
    read = _notification().model_copy(update={"is_read": True, "read_at": NOW})
    repository.get_notification.side_effect = [detail, None]
    repository.mark_notification_read.side_effect = [read, None]
    repository.count_unread.return_value = 3
    badge_summary = WorkOrderNotificationBadgeSummary(
        unread_count=3,
        pending_approval_count=2,
        unread_notice_count=1,
        badge_count=3,
    )
    repository.get_notification_badge_summary.return_value = badge_summary
    repository.mark_all_notifications_read.return_value = 2

    assert service.get_detail(notification_id=21, actor_id="owner-1") == detail
    with pytest.raises(WorkOrderNotificationNotFoundError):
        service.get_detail(notification_id=22, actor_id="owner-1")
    assert service.unread_count(actor_id="owner-1") == 3
    assert service.badge_summary(actor_id="owner-1") == badge_summary
    assert service.mark_read(notification_id=21, actor_id="owner-1") == read
    with pytest.raises(WorkOrderNotificationNotFoundError):
        service.mark_read(notification_id=22, actor_id="owner-1")
    assert service.mark_all_read(actor_id="owner-1") == 2

    repository.get_notification.assert_any_call(
        notification_id=21,
        recipient_user_id="owner-1",
        env="dev",
        mark_read=True,
    )
    repository.count_unread.assert_called_once_with(
        recipient_user_id="owner-1", env="dev"
    )
    repository.get_notification_badge_summary.assert_called_once_with(
        recipient_user_id="owner-1", env="dev"
    )
    repository.mark_all_notifications_read.assert_called_once_with(
        recipient_user_id="owner-1", env="dev"
    )


def test_event_categories_include_all_application_events():
    assert APPROVAL_EVENT_TYPES == frozenset(
        {
            WorkOrderEventType.SPACE_JOIN_APPLIED,
            WorkOrderEventType.BOT_COLLABORATOR_APPLIED,
            WorkOrderEventType.SKILL_COLLABORATOR_APPLIED,
            WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED,
            WorkOrderEventType.BOT2BOT_FRIEND_APPLIED,
        }
    )
    assert all(
        EVENT_CATEGORIES[event_type] is NotificationCategory.APPROVAL
        for event_type in APPROVAL_EVENT_TYPES
    )


@pytest.mark.parametrize(
    ("category", "event_type", "applicant", "approvers", "recipients"),
    [
        (
            NotificationCategory.APPROVAL,
            "SPACE_JOIN_APPLIED",
            "actor-1",
            ["approver-1", "approver-1"],
            [],
        ),
        (
            NotificationCategory.NOTICE,
            "SPACE_JOIN_REVIEWED",
            None,
            [],
            ["recipient-1", "recipient-1"],
        ),
    ],
)
def test_create_work_order_event_normalizes_and_delegates(
    category: NotificationCategory,
    event_type: str,
    applicant: str | None,
    approvers: list[str],
    recipients: list[str],
) -> None:
    service, repository, _, _, _ = _service()
    expected = WorkOrderEventCreatedResult(
        event_category=category,
        work_order_id=11 if category is NotificationCategory.APPROVAL else None,
        work_order_no="WO-11" if category is NotificationCategory.APPROVAL else None,
        notification_ids=[21],
        status=WorkOrderEventStatus.CREATED,
    )
    repository.create_work_order_event.return_value = expected

    result = service.create_work_order_event(
        event_category=category,
        biz_type="  SPACE_JOIN  ",
        biz_id=" 7 ",
        event_type=event_type,
        applicant_user_id=applicant,
        approver_user_ids=approvers,
        recipient_user_ids=recipients,
        title="  title  ",
        content={"message": "content", "items": [1, True]},
        apply_reason="  reason  ",
        biz_data={"space_id": 7},
        actor_id="actor-1",
    )

    assert result == expected
    repository.create_work_order_event.assert_called_once_with(
        event_category=category,
        biz_type="SPACE_JOIN",
        biz_id="7",
        event_type=event_type,
        applicant_user_id="actor-1"
        if category is NotificationCategory.APPROVAL
        else None,
        approver_user_ids=approvers[:1]
        if category is NotificationCategory.APPROVAL
        else [],
        recipient_user_ids=recipients[:1]
        if category is NotificationCategory.NOTICE
        else [],
        title=("SPACE_JOIN_PENDING" if event_type == "SPACE_JOIN_APPLIED" else "title"),
        content='{"message": "content", "items": [1, true]}',
        apply_reason="reason",
        biz_data='{"space_id": 7}',
        env="dev",
    )


def test_manual_skill_event_is_not_blocked_by_generic_work_order_service() -> None:
    service, repository, _, _, _ = _service()
    repository.create_work_order_event.return_value = WorkOrderEventCreatedResult(
        event_category=NotificationCategory.APPROVAL,
        work_order_id=42,
        work_order_no="WO-42",
        notification_ids=[24],
        status=WorkOrderEventStatus.PENDING,
    )

    result = service.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        biz_type=WorkOrderBizType.SKILL_COLLABORATOR.value,
        biz_id="91",
        event_type=WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
        applicant_user_id="actor-1",
        approver_user_ids=["owner-1"],
        recipient_user_ids=[],
        title="Skill editor request",
        content=None,
        apply_reason=None,
        biz_data={"space_id": 7, "skill_id": 91},
        actor_id="actor-1",
    )

    assert result.status is WorkOrderEventStatus.PENDING
    repository.create_work_order_event.assert_called_once()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"event_type": "UNKNOWN"}, "not registered"),
        (
            {
                "event_category": NotificationCategory.APPROVAL,
                "event_type": "SPACE_JOIN_REVIEWED",
            },
            "does not match",
        ),
        (
            {
                "event_category": NotificationCategory.APPROVAL,
                "approver_user_ids": [],
                "recipient_user_ids": ["recipient-1"],
            },
            "require approvers",
        ),
        ({"applicant_user_id": "other-user"}, "applicant must be"),
        ({"apply_reason": "x" * 513}, "no more than 512"),
    ],
)
def test_create_work_order_event_rejects_invalid_input(
    overrides: dict[str, object], message: str
) -> None:
    service, repository, _, _, _ = _service()
    payload: dict[str, object] = {
        "event_category": NotificationCategory.APPROVAL,
        "biz_type": "SPACE_JOIN",
        "biz_id": "7",
        "event_type": "SPACE_JOIN_APPLIED",
        "applicant_user_id": "actor-1",
        "approver_user_ids": ["approver-1"],
        "recipient_user_ids": [],
        "title": "title",
        "content": None,
        "apply_reason": "reason",
        "biz_data": None,
        "actor_id": "actor-1",
    }
    payload.update(overrides)

    with pytest.raises(
        (WorkOrderInvalidEventError, WorkOrderAccessDeniedError), match=message
    ):
        service.create_work_order_event(**payload)  # type: ignore[arg-type]

    repository.create_work_order_event.assert_not_called()


@pytest.mark.parametrize(
    "source_event_type",
    [None, WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value],
)
def test_friend_approval_rejects_missing_or_unsupported_source_event(
    source_event_type: str | None,
) -> None:
    callbacks = MagicMock(spec=WorkOrderDecisionCallbackDispatcher)
    service, repository, _, _, _ = _service(decision_callbacks=callbacks)
    context = _friend_context().model_copy(
        update={"source_event_type": source_event_type}
    )
    repository.get_detail.return_value = _detail().model_copy(
        update={"work_order": context.work_order}
    )
    repository.get_approval_context.return_value = context

    with pytest.raises(WorkOrderInvalidEventError):
        service.process_approval(
            work_order_id=11,
            actor_id="owner-1",
            decision=WorkOrderDecision.APPROVED,
            review_remark=None,
            callback_credential=WorkOrderCallbackCredential(headers={}),
        )

    callbacks.dispatch.assert_not_called()
    repository.process_approval.assert_not_called()


def test_auto_skill_uses_skill_transaction_without_duplicate_work_order_finalize():
    service, repo, *_ = _service()
    repo.create_work_order_event.return_value = WorkOrderEventCreatedResult(
        event_category=NotificationCategory.APPROVAL,
        work_order_id=19,
        work_order_no="WO-19",
        notification_ids=[],
        status=WorkOrderEventStatus.PENDING,
    )
    result = service.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        approval_mode=WorkOrderApprovalMode.AUTO,
        biz_type=WorkOrderBizType.SKILL_COLLABORATOR.value,
        biz_id="skill-1",
        event_type=WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
        applicant_user_id="actor",
        approver_user_ids=['actor'],
        recipient_user_ids=[],
        title="AUTO request",
        content=None,
        apply_reason=None,
        biz_data={},
        actor_id="actor",
    )

    assert result.status is WorkOrderEventStatus.APPROVED
    repo.apply_auto_skill_editor_request.assert_called_once_with(
        work_order_id=19,
        recipient_user_ids=["actor"],
        source_event_type=WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
        env="dev",
    )
    repo.finalize_auto_approval.assert_not_called()
    repo.create_auto_result_notifications.assert_not_called()


def test_auto_skill_non_approved_handler_result_marks_order_failed():
    service, repo, *_ = _service()
    repo.fail_auto_approval.return_value = [34]
    repo.create_work_order_event.return_value = WorkOrderEventCreatedResult(
        event_category=NotificationCategory.APPROVAL,
        work_order_id=20,
        work_order_no="WO-20",
        notification_ids=[],
        status=WorkOrderEventStatus.PENDING,
    )
    repo.apply_auto_skill_editor_request.side_effect = RuntimeError(
        "skill transaction failed"
    )

    result = service.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        approval_mode=WorkOrderApprovalMode.AUTO,
        biz_type=WorkOrderBizType.SKILL_COLLABORATOR.value,
        biz_id="skill-2",
        event_type=WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
        applicant_user_id="actor",
        approver_user_ids=['actor'],
        recipient_user_ids=[],
        title="AUTO request",
        content=None,
        apply_reason=None,
        biz_data={},
        actor_id="actor",
    )

    assert result.status is WorkOrderEventStatus.FAILED
    repo.fail_auto_approval.assert_called_once()
    assert (
        "RuntimeError"
        in repo.fail_auto_approval.call_args.kwargs["review_remark"]
    )
    assert repo.fail_auto_approval.call_args.kwargs["recipient_user_ids"] == ["actor"]
    repo.complete_auto_approval.assert_not_called()
    assert result.notification_ids == [34]
