"""Unit tests for work-order orchestration and recipient notifications."""

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.spaces.errors import SpaceAccessDeniedError
from agentclaw.community.core.spaces.models import SpaceRecord, SpaceType
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderApplicantAlreadyEditorError,
    WorkOrderApplicantAlreadyMemberError,
    WorkOrderInvalidReasonError,
    WorkOrderInvalidRemarkError,
    WorkOrderJoinNotAllowedError,
    WorkOrderNotificationNotFoundError,
    WorkOrderNotFoundError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderBizType,
    WorkOrderDetail,
    WorkOrderDecision,
    APPROVAL_EVENT_TYPES,
    EVENT_CATEGORIES,
    WorkOrderEventType,
    WorkOrderItemType,
    WorkOrderNotificationDetail,
    WorkOrderNotificationDraft,
    WorkOrderNotificationBadgeSummary,
    WorkOrderNotificationRecord,
    WorkOrderQueryType,
    WorkOrderRecord,
    WorkOrderReviewResult,
    WorkOrderStatus,
)
from agentclaw.community.core.work_orders.services.work_order_service import (
    WorkOrderNotificationService,
    WorkOrderService,
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


def _service():
    repository = MagicMock()
    spaces = MagicMock()
    access = MagicMock()
    notifications = MagicMock(spec=WorkOrderNotificationService)
    bots = MagicMock()
    collaborator_repository = MagicMock()
    collaborators = MagicMock()
    member_management = MagicMock()
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
    service = WorkOrderService(
        repository,
        spaces,
        access,
        notifications,
        bots,
        collaborator_repository,
        collaborators,
        member_management,
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
        content="approved",
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


@pytest.mark.parametrize("value", ["", "   ", "x" * 513])
def test_create_rejects_invalid_reason(value: str) -> None:
    service, repository, _, _, _ = _service()

    with pytest.raises(WorkOrderInvalidReasonError, match="1-512"):
        service.create_space_join_request(
            space_id=7, applicant_user_id="applicant-1", reason=value
        )

    repository.create_space_join_request.assert_not_called()


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


def test_create_rejects_personal_space() -> None:
    service, repository, spaces, access, _ = _service()
    access.require_space.return_value = _space(SpaceType.PERSONAL)

    with pytest.raises(WorkOrderJoinNotAllowedError):
        service.create_space_join_request(
            space_id=7, applicant_user_id="applicant-1", reason="join"
        )

    spaces.get_member.assert_not_called()
    repository.create_space_join_request.assert_not_called()


def test_create_rejects_existing_member() -> None:
    service, repository, spaces, access, _ = _service()
    access.require_space.return_value = _space()
    spaces.get_member.return_value = object()

    with pytest.raises(WorkOrderApplicantAlreadyMemberError):
        service.create_space_join_request(
            space_id=7, applicant_user_id="applicant-1", reason="join"
        )

    repository.create_space_join_request.assert_not_called()


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
        content="result",
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
        content="approved",
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
            "空间加入申请已通过",
            "你加入空间「Team」的申请已通过。",
        ),
        (
            WorkOrderStatus.REJECTED,
            "空间加入申请未通过",
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
        content=expected_content,
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
