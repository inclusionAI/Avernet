"""Unit tests for work-order orchestration and recipient notifications."""

import json
from datetime import datetime
from unittest.mock import MagicMock, call

import pytest

from agentclaw.community.core.spaces.errors import SpaceAccessDeniedError
from agentclaw.community.core.spaces.models import SpaceRecord, SpaceType
from agentclaw.community.core.work_orders.callbacks import (
    WorkOrderCallbackCredential,
    WorkOrderDecisionCallbackDispatcher,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyProcessedError,
    WorkOrderCallbackError,
    WorkOrderApplicantAlreadyEditorError,
    WorkOrderApplicantAlreadyMemberError,
    WorkOrderInvalidReasonError,
    WorkOrderInvalidRemarkError,
    WorkOrderInvalidEventError,
    WorkOrderJoinNotAllowedError,
    WorkOrderNotificationNotFoundError,
    WorkOrderNotFoundError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalContext,
    WorkOrderApproverRecord,
    WorkOrderApproverStatus,
    WorkOrderBizType,
    WorkOrderDetail,
    WorkOrderDecision,
    APPROVAL_EVENT_TYPES,
    EVENT_CATEGORIES,
    WorkOrderEventType,
    WorkOrderEventStatus,
    WorkOrderEventCreatedResult,
    WorkOrderItemType,
    WorkOrderListItem,
    WorkOrderNotificationDetail,
    WorkOrderNotificationDraft,
    WorkOrderNotificationBadgeSummary,
    WorkOrderNotificationRecord,
    WorkOrderQueryType,
    WorkOrderRecord,
    WorkOrderReviewResult,
    WorkOrderStatus,
    WorkOrderTitleKey,
)
from agentclaw.community.core.work_orders.services.work_order_service import (
    WorkOrderNotificationService,
    WorkOrderService,
)
from agentclaw.community.plugin_api.staff_dept import (
    StaffDeptPlugin,
    StaffProfileInfo,
    StaffProfileLookupError,
)

NOW = datetime(2026, 8, 18, 8, 0, 0)


def _space(space_type: SpaceType = SpaceType.TEAM) -> SpaceRecord:
    return SpaceRecord(
        id=7,
        space_code="spc-7",
        space_type=space_type,
        name="Team",
        personal_owner_id=None,
        env="dev",
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
        env="dev",
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


def _friend_context(
    *,
    event_type: WorkOrderEventType = WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED,
    order_status: WorkOrderStatus = WorkOrderStatus.PENDING,
    approver_status: WorkOrderApproverStatus = WorkOrderApproverStatus.PENDING,
) -> WorkOrderApprovalContext:
    order = _work_order().model_copy(
        update={
            "biz_type": WorkOrderBizType.BOT_FRIEND,
            "biz_id": "friend-request",
            "biz_data": json.dumps({"request_ids": ["request-77"]}),
            "status": order_status,
        }
    )
    return WorkOrderApprovalContext(
        work_order=order,
        approver=WorkOrderApproverRecord(
            id=31,
            work_order_id=order.id,
            approver_user_id="owner-1",
            status=approver_status,
            review_remark=None,
            reviewed_at=None,
            env="dev",
            gmt_created=NOW,
            gmt_modified=NOW,
        ),
        source_event_type=event_type.value,
    )


def _notification() -> WorkOrderNotificationRecord:
    return WorkOrderNotificationRecord(
        id=21,
        work_order_id=11,
        recipient_user_id="owner-1",
        notification_category=NotificationCategory.APPROVAL,
        event_type=WorkOrderEventType.SPACE_JOIN_APPLIED,
        biz_type=WorkOrderBizType.SPACE_JOIN,
        biz_id="7",
        title="pending",
        content="content",
        is_read=False,
        read_at=None,
        env="dev",
        gmt_created=NOW,
        gmt_modified=NOW,
    )


def _service(
    *,
    staff_dept: StaffDeptPlugin | None = None,
    decision_callbacks: WorkOrderDecisionCallbackDispatcher | None = None,
):
    repository = MagicMock()
    spaces = MagicMock()
    access = MagicMock()
    notifications = MagicMock(spec=WorkOrderNotificationService)
    bots = MagicMock()
    collaborator_repository = MagicMock()
    collaborators = MagicMock()
    member_management = MagicMock()
    skill_handler = MagicMock()
    if staff_dept is None:
        staff_dept = MagicMock(spec=StaffDeptPlugin)
        staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
            work_no="applicant-1",
            nick_name=None,
        )
    if decision_callbacks is None:
        decision_callbacks = MagicMock(spec=WorkOrderDecisionCallbackDispatcher)
        decision_callbacks.requires_callback.return_value = False
    return (
        WorkOrderService(
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
        ),
        repository,
        spaces,
        access,
        notifications,
    )


def _bot_editor_service():
    repository = MagicMock()
    spaces = MagicMock()
    access = MagicMock()
    notifications = MagicMock(spec=WorkOrderNotificationService)
    bots = MagicMock()
    collaborator_repository = MagicMock()
    collaborators = MagicMock()
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
    )
    return (
        service,
        repository,
        access,
        notifications,
        bots,
        collaborator_repository,
        collaborators,
        member_management,
    )


def test_create_bot_editor_request_enforces_eligibility_and_delegates() -> None:
    (
        service,
        repository,
        access,
        _,
        bots,
        collaborator_repository,
        _,
        member_management,
    ) = _bot_editor_service()
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
    collaborator_repository.get_by_bot_and_user.return_value = None
    repository.create_bot_editor_request.return_value = _work_order()

    result = service.create_bot_editor_request(
        bot_id="bot-17",
        owner_id="owner-1",
        applicant_user_id="applicant-1",
        reason="  joint editing  ",
    )

    assert result == _work_order()
    access.require_space_member.assert_called_once_with(
        space_id=7, user_id="applicant-1"
    )
    repository.create_bot_editor_request.assert_called_once_with(
        bot_pk=17,
        bot_id="bot-17",
        bot_name="Editor Bot",
        owner_id="owner-1",
        space_id=7,
        applicant_user_id="applicant-1",
        applicant_name="applicant-1",
        apply_reason="joint editing",
        env="dev",
    )


def test_bot_owner_cannot_request_editor_access() -> None:
    service, repository, _, _, bots, _, _, _ = _bot_editor_service()
    bots.get_by_id_and_owner.return_value = {
        "id": 17,
        "bot_id": "bot-17",
        "owner_id": "owner-1",
    }

    with pytest.raises(WorkOrderApplicantAlreadyEditorError):
        service.create_bot_editor_request(
            bot_id="bot-17",
            owner_id="owner-1",
            applicant_user_id="owner-1",
            reason="joint editing",
        )

    repository.create_bot_editor_request.assert_not_called()


def test_unified_approval_dispatches_bot_editor_side_effect() -> None:
    service, repository, _, notifications, _, _, collaborators, _ = (
        _bot_editor_service()
    )
    work_order = _work_order().model_copy(
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
    detail = _detail().model_copy(update={"work_order": work_order})
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
    repository.review_bot_editor_request.assert_called_once_with(
        work_order_id=11,
        reviewer_user_id="owner-1",
        review_remark=None,
        target_status=WorkOrderStatus.APPROVED,
        notification=notification,
        env="dev",
    )
    collaborators.on_collaboration_changed.assert_called_once_with(
        "bot-17", "owner-1", "dev"
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


def test_generic_approval_without_handler_preserves_existing_path() -> None:
    callbacks = MagicMock(spec=WorkOrderDecisionCallbackDispatcher)
    callbacks.requires_callback.return_value = False
    service, repository, _, _, _ = _service(decision_callbacks=callbacks)
    context = _friend_context().model_copy(
        update={
            "work_order": _work_order().model_copy(
                    update={"biz_type": "GENERIC_APPROVAL", "biz_id": "generic-1"}
            ),
            "source_event_type": WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
        }
    )
    repository.get_detail.return_value = _detail().model_copy(
        update={"work_order": context.work_order}
    )
    repository.get_approval_context.return_value = context
    repository.process_approval.return_value = MagicMock()

    service.process_approval(
        work_order_id=11,
        actor_id="owner-1",
        decision=WorkOrderDecision.APPROVED,
        review_remark=None,
        callback_credential=WorkOrderCallbackCredential(headers={}),
    )

    callbacks.dispatch.assert_not_called()
    repository.process_approval.assert_called_once()


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


@pytest.mark.parametrize("value", ["x" * 513])
def test_create_rejects_invalid_reason(value: str) -> None:
    service, repository, _, _, _ = _service()

    with pytest.raises(WorkOrderInvalidReasonError, match="512"):
        service.create_space_join_request(
            space_id=7, applicant_user_id="applicant-1", reason=value
        )

    repository.create_space_join_request.assert_not_called()


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_create_space_join_request_allows_blank_reason_as_null(
    reason: str | None,
) -> None:
    service, repository, spaces, access, _ = _service()
    access.require_space.return_value = _space()
    spaces.get_member.return_value = None
    repository.create_space_join_request.return_value = _work_order()

    service.create_space_join_request(
        space_id=7, applicant_user_id="applicant-1", reason=reason
    )

    repository.create_space_join_request.assert_called_once_with(
        space_id=7,
        applicant_user_id="applicant-1",
        applicant_name="applicant-1",
        apply_reason=None,
        env="dev",
    )


def test_create_space_join_request_normalizes_and_delegates() -> None:
    service, repository, spaces, access, _ = _service()
    access.require_space.return_value = _space()
    spaces.get_member.return_value = None
    repository.create_space_join_request.return_value = _work_order()

    result = service.create_space_join_request(
        space_id=7, applicant_user_id="applicant-1", reason="  join  "
    )

    assert result == _work_order()
    access.require_space.assert_called_once_with(space_id=7)
    spaces.get_member.assert_called_once_with(
        space_id=7, user_id="applicant-1", env="dev"
    )
    repository.create_space_join_request.assert_called_once_with(
        space_id=7,
        applicant_user_id="applicant-1",
        applicant_name="applicant-1",
        apply_reason="join",
        env="dev",
    )


def test_create_space_join_request_uses_staff_nickname() -> None:
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="applicant-1",
        nick_name="  花花  ",
    )
    service, repository, spaces, access, _ = _service(staff_dept=staff_dept)
    access.require_space.return_value = _space()
    spaces.get_member.return_value = None
    repository.create_space_join_request.return_value = _work_order()

    service.create_space_join_request(
        space_id=7, applicant_user_id="1234", reason="join"
    )

    staff_dept.get_profile_by_work_no.assert_called_once_with(work_no="001234")
    assert (
        repository.create_space_join_request.call_args.kwargs["applicant_name"]
        == "花花"
    )


@pytest.mark.parametrize("nickname", [None, "", "   "])
def test_create_space_join_request_falls_back_when_nickname_is_missing(
    nickname: str | None,
) -> None:
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="applicant-1",
        nick_name=nickname,
    )
    service, repository, spaces, access, _ = _service(staff_dept=staff_dept)
    access.require_space.return_value = _space()
    spaces.get_member.return_value = None

    service.create_space_join_request(
        space_id=7, applicant_user_id="applicant-1", reason="join"
    )

    assert (
        repository.create_space_join_request.call_args.kwargs["applicant_name"]
        == "applicant-1"
    )


def test_create_space_join_request_falls_back_when_staff_lookup_fails() -> None:
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.side_effect = StaffProfileLookupError("down")
    service, repository, spaces, access, _ = _service(staff_dept=staff_dept)
    access.require_space.return_value = _space()
    spaces.get_member.return_value = None

    service.create_space_join_request(
        space_id=7, applicant_user_id="applicant-1", reason="join"
    )

    assert (
        repository.create_space_join_request.call_args.kwargs["applicant_name"]
        == "applicant-1"
    )


def test_create_space_join_request_truncates_staff_nickname() -> None:
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="applicant-1",
        nick_name="花" * 129,
    )
    service, repository, spaces, access, _ = _service(staff_dept=staff_dept)
    access.require_space.return_value = _space()
    spaces.get_member.return_value = None

    service.create_space_join_request(
        space_id=7, applicant_user_id="applicant-1", reason="join"
    )

    assert (
        repository.create_space_join_request.call_args.kwargs["applicant_name"]
        == "花" * 128
    )


def test_create_rejects_personal_space() -> None:
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    service, repository, spaces, access, _ = _service(staff_dept=staff_dept)
    access.require_space.return_value = _space(SpaceType.PERSONAL)

    with pytest.raises(WorkOrderJoinNotAllowedError):
        service.create_space_join_request(
            space_id=7, applicant_user_id="applicant-1", reason="join"
        )

    spaces.get_member.assert_not_called()
    repository.create_space_join_request.assert_not_called()
    staff_dept.get_profile_by_work_no.assert_not_called()


def test_create_rejects_existing_member() -> None:
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    service, repository, spaces, access, _ = _service(staff_dept=staff_dept)
    access.require_space.return_value = _space()
    spaces.get_member.return_value = object()

    with pytest.raises(WorkOrderApplicantAlreadyMemberError):
        service.create_space_join_request(
            space_id=7, applicant_user_id="applicant-1", reason="join"
        )

    repository.create_space_join_request.assert_not_called()
    staff_dept.get_profile_by_work_no.assert_not_called()


def test_list_items_forwards_filters_and_normalizes_pagination() -> None:
    service, repository, _, _, _ = _service()
    repository.list_items.return_value = (0, [])

    assert service.list_items(
        actor_id="owner-1",
        query_type=WorkOrderQueryType.PROCESSED_BY_ME,
        item_type=WorkOrderItemType.NOTICE,
        page_no=3,
        page_size=10,
    ) == (0, [])
    repository.list_items.assert_called_once_with(
        actor_id="owner-1",
        env="dev",
        query_type=WorkOrderQueryType.PROCESSED_BY_ME,
        item_type=WorkOrderItemType.NOTICE,
        biz_type=None,
        biz_id=None,
        offset=20,
        limit=10,
    )


def test_list_items_refreshes_historical_skill_editor_request_copy() -> None:
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="200177", nick_name="张三"
    )
    service, repository, _, _, _ = _service(staff_dept=staff_dept)
    work_order = _work_order().model_copy(
        update={
            "biz_type": WorkOrderBizType.SKILL_COLLABORATOR,
            "biz_id": "1123982",
            "applicant_user_id": "200177",
            "biz_data": json.dumps({"skill_name": "qa"}),
        }
    )
    notification = _notification().model_copy(
        update={
            "event_type": WorkOrderEventType.SKILL_COLLABORATOR_APPLIED,
            "biz_type": WorkOrderBizType.SKILL_COLLABORATOR,
            "biz_id": "1123982",
            "content": "用户「200177」申请共同编辑 Skill「qa」，请及时处理。",
        }
    )
    repository.list_items.return_value = (
        1,
        [
            WorkOrderListItem(
                work_order=work_order,
                notification=notification,
                can_approve=True,
            )
        ],
    )

    _, items = service.list_items(
        actor_id="owner-1",
        query_type=WorkOrderQueryType.PENDING_FOR_ME,
        item_type=WorkOrderItemType.APPROVAL,
        page_no=1,
        page_size=10,
    )

    assert items[0].notification is not None
    assert (
        items[0].notification.content
        == "用户「张三」(200177)申请共同编辑 Skill「qa」，请及时处理。"
    )
    assert (
        notification.content == "用户「200177」申请共同编辑 Skill「qa」，请及时处理。"
    )
    staff_dept.get_profile_by_work_no.assert_called_once_with(work_no="200177")


def test_get_detail_resolves_reviewer_name_from_staff_directory() -> None:
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="001234", nick_name=" Reviewer "
    )
    service, repository, *_ = _service(staff_dept=staff_dept)
    repository.get_detail.return_value = _detail().model_copy(
        update={
            "work_order": _work_order().model_copy(
                update={"reviewer_user_id": "1234"}
            )
        }
    )

    detail = service.get_detail(work_order_id=11, actor_id="owner-1")

    assert detail.reviewer_user_name == "Reviewer"
    staff_dept.get_profile_by_work_no.assert_called_once_with(work_no="001234")


def test_get_detail_keeps_success_when_reviewer_lookup_fails() -> None:
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.side_effect = StaffProfileLookupError("down")
    service, repository, *_ = _service(staff_dept=staff_dept)
    repository.get_detail.return_value = _detail().model_copy(
        update={
            "work_order": _work_order().model_copy(
                update={"reviewer_user_id": "1234"}
            )
        }
    )

    detail = service.get_detail(work_order_id=11, actor_id="owner-1")

    assert detail.reviewer_user_name is None


def test_get_detail_returns_record_and_missing_raises() -> None:
    service, repository, _, _, _ = _service()
    repository.get_detail.side_effect = [_detail(), None]

    assert service.get_detail(work_order_id=11, actor_id="owner-1") == _detail()
    with pytest.raises(WorkOrderNotFoundError):
        service.get_detail(work_order_id=12, actor_id="owner-1")


@pytest.mark.parametrize(
    ("method", "status"),
    [("approve", WorkOrderStatus.APPROVED), ("reject", WorkOrderStatus.REJECTED)],
)
def test_review_requires_owner_and_delegates(
    method: str, status: WorkOrderStatus
) -> None:
    service, repository, _, access, notifications = _service()
    repository.get_detail.return_value = _detail()
    notification = WorkOrderNotificationDraft(
        recipient_user_id="applicant-1",
        notification_category=NotificationCategory.NOTICE,
        event_type=WorkOrderEventType.SPACE_JOIN_REVIEWED,
        biz_type=WorkOrderBizType.SPACE_JOIN,
        biz_id="7",
        title="reviewed",
        content={"text": "result"},
    )
    notifications.build_space_join_review_result.return_value = notification
    expected = WorkOrderReviewResult(
        work_order_id=11,
        status=status,
        reviewer_user_id="owner-1",
        review_remark="ok",
        reviewed_at=NOW,
    )
    repository.review_space_join.return_value = expected

    result = getattr(service, method)(
        work_order_id=11, actor_id="owner-1", review_remark="  ok  "
    )

    assert result == expected
    access.require_space_owner.assert_called_once_with(space_id=7, user_id="owner-1")
    notifications.build_space_join_review_result.assert_called_once_with(
        detail=_detail(),
        target_status=status,
        review_remark="ok",
    )
    repository.review_space_join.assert_called_once_with(
        work_order_id=11,
        reviewer_user_id="owner-1",
        review_remark="ok",
        target_status=status,
        notification=notification,
        applicant_user_name=("applicant-1" if status is WorkOrderStatus.APPROVED else None),
        env="dev",
    )


@pytest.mark.parametrize("value", [None, "", "   "])
def test_approve_accepts_missing_or_blank_remark(value: str | None) -> None:
    service, repository, _, access, notifications = _service()
    repository.get_detail.return_value = _detail()
    notification = WorkOrderNotificationDraft(
        recipient_user_id="applicant-1",
        notification_category=NotificationCategory.NOTICE,
        event_type=WorkOrderEventType.SPACE_JOIN_REVIEWED,
        biz_type=WorkOrderBizType.SPACE_JOIN,
        biz_id="7",
        title="approved",
        content={"text": "approved"},
    )
    notifications.build_space_join_review_result.return_value = notification
    expected = WorkOrderReviewResult(
        work_order_id=11,
        status=WorkOrderStatus.APPROVED,
        reviewer_user_id="owner-1",
        review_remark=None,
        reviewed_at=NOW,
    )
    repository.review_space_join.return_value = expected

    assert (
        service.approve(work_order_id=11, actor_id="owner-1", review_remark=value)
        == expected
    )
    access.require_space_owner.assert_called_once_with(space_id=7, user_id="owner-1")
    repository.review_space_join.assert_called_once_with(
        work_order_id=11,
        reviewer_user_id="owner-1",
        review_remark=None,
        target_status=WorkOrderStatus.APPROVED,
        notification=notification,
        applicant_user_name="applicant-1",
        env="dev",
    )


def test_approve_rejects_oversized_remark() -> None:
    service, repository, _, _, _ = _service()

    with pytest.raises(WorkOrderInvalidRemarkError, match="512"):
        service.approve(work_order_id=11, actor_id="owner-1", review_remark="x" * 513)

    repository.get_detail.assert_not_called()


@pytest.mark.parametrize("value", [None, "", "   ", "x" * 513])
def test_reject_requires_valid_remark(value: str | None) -> None:
    service, repository, _, _, _ = _service()

    with pytest.raises(WorkOrderInvalidRemarkError, match="1-512"):
        service.reject(work_order_id=11, actor_id="owner-1", review_remark=value)

    repository.get_detail.assert_not_called()


def test_review_maps_space_access_denied_to_work_order_error() -> None:
    service, repository, _, access, notifications = _service()
    repository.get_detail.return_value = _detail()
    access.require_space_owner.side_effect = SpaceAccessDeniedError("private")

    with pytest.raises(WorkOrderAccessDeniedError) as exc_info:
        service.reject(work_order_id=11, actor_id="member-1", review_remark="no")

    assert isinstance(exc_info.value.__cause__, SpaceAccessDeniedError)
    notifications.build_space_join_review_result.assert_not_called()
    repository.review_space_join.assert_not_called()


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
        (
            {
                "biz_type": "SKILL_COLLABORATOR",
                "event_type": "SKILL_COLLABORATOR_APPLIED",
            },
            "must use the Skill endpoint",
        ),
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
