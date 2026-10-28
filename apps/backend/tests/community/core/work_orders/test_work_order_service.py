"""Unit tests for work-order orchestration and recipient notifications."""

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from tests.community.bot_address_fakes import support_legacy_locator
from tests.community.core.bot_collaborator.manager_sync.test_collaborator_triggers import (
    RecordingNotifier,
)

from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    DisabledBotManagerSync,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
)
from agentclaw.community.core.spaces.errors import SpaceAccessDeniedError
from agentclaw.community.core.spaces.models import SpaceRecord, SpaceType
from agentclaw.community.core.work_orders.callbacks import (
    WorkOrderCallbackCredential,
    WorkOrderDecisionCallbackDispatcher,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderApplicantAlreadyEditorError,
    WorkOrderApplicantAlreadyMemberError,
    WorkOrderInvalidReasonError,
    WorkOrderInvalidRemarkError,
    WorkOrderJoinNotAllowedError,
    WorkOrderNotFoundError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalContext,
    WorkOrderApprovalMode,
    WorkOrderApproverRecord,
    WorkOrderApproverStatus,
    WorkOrderBizType,
    WorkOrderDetail,
    WorkOrderDecision,
    WorkOrderEventCreatedResult,
    WorkOrderEventStatus,
    WorkOrderEventType,
    WorkOrderItemType,
    WorkOrderListItem,
    WorkOrderNotificationDraft,
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
from agentclaw.community.kernel.bot_manager_sync import (
    SyncKey,
    SyncReason,
)
from agentclaw.community.plugin_api.staff_dept import (
    StaffDeptPlugin,
    StaffProfileInfo,
    StaffProfileLookupError,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

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
            DisabledBotManagerSync(),
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
        DisabledBotManagerSync(),
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


@pytest.mark.parametrize("auto_approved", [False, True])
def test_create_bot_editor_request_enforces_eligibility_and_delegates(
    auto_approved,
) -> None:
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
    record = _work_order()
    if auto_approved:
        record = record.model_copy(update={"status": WorkOrderStatus.APPROVED})
    repository.create_bot_editor_request.return_value = record

    result = service.create_bot_editor_request(
        bot_id="bot-17",
        owner_id="owner-1",
        applicant_user_id="applicant-1",
        reason="  joint editing  ",
    )

    assert result == record
    if auto_approved:
        service._collaborators.on_collaboration_changed.assert_called_once_with(
            "bot-17", "owner-1", "dev"
        )
    else:
        service._collaborators.on_collaboration_changed.assert_not_called()
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


def test_editor_request_policy_delegates_authenticated_actor_and_environment() -> None:
    service, repository, *_ = _bot_editor_service()
    repository.get_bot_editor_request_policy.return_value = False
    repository.update_bot_editor_request_policy.return_value = True
    identity = dict(bot_id="bot-1", owner_id="owner-1", actor_id="actor-1")
    assert service.get_bot_editor_request_policy(**identity) is False
    repository.get_bot_editor_request_policy.assert_called_once_with(
        **identity, env="dev"
    )
    assert (
        service.update_bot_editor_request_policy(**identity, auto_approve=True) is True
    )
    repository.update_bot_editor_request_policy.assert_called_once_with(
        **identity, auto_approve=True, env="dev"
    )


def test_create_bot_editor_request_allows_team_space_bot_without_engine_capability() -> (
    None
):
    """A Team Space Bot accepts editor requests regardless of engine/template form.

    真实缺陷（预发 2026-09-09）：bot 20260902_07czoyk5 为 bot_type=personal、
    active_engine=openclaw、template_type=None，被 can_manage_collaborators
    以 "Bot does not support editor management" 拒绝。Team Space 的编辑协作契约
    由空间归属决定（成员申请、Owner 审批、协作者直插），不依赖引擎能力标记。
    """
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
        "bot_name": "Team Plain Bot",
        "bot_type": "personal",
        "owner_id": "owner-1",
        "space_id": 7,
        "active_engine": "openclaw",
        "template_type": None,
    }
    member_management.can_manage_collaborators.return_value = False
    access.require_space_reference.return_value = _space()
    collaborator_repository.get_by_bot_and_user.return_value = None
    repository.create_bot_editor_request.return_value = _work_order()

    result = service.create_bot_editor_request(
        bot_id="bot-17",
        owner_id="owner-1",
        applicant_user_id="applicant-1",
        reason="joint editing",
    )

    assert result == _work_order()
    access.require_space_member.assert_called_once_with(
        space_id=7, user_id="applicant-1"
    )
    repository.create_bot_editor_request.assert_called_once_with(
        bot_pk=17,
        bot_id="bot-17",
        bot_name="Team Plain Bot",
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
            "work_order": _work_order().model_copy(update={"reviewer_user_id": "1234"})
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
            "work_order": _work_order().model_copy(update={"reviewer_user_id": "1234"})
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
    access.require_space_owner.return_value = (_space(), MagicMock())
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
        applicant_user_name=(
            "applicant-1" if status is WorkOrderStatus.APPROVED else None
        ),
        env="dev",
    )


@pytest.mark.parametrize("value", [None, "", "   "])
def test_approve_accepts_missing_or_blank_remark(value: str | None) -> None:
    service, repository, _, access, notifications = _service()
    repository.get_detail.return_value = _detail()
    access.require_space_owner.return_value = (_space(), MagicMock())
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
