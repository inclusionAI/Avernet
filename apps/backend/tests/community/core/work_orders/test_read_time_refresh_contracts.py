"""Read-time presentation refresh must not disturb persisted contracts.

The nickname refresh projects over repository rows at read time. These tests
pin the surrounding contracts — persisted content/biz_data, query and message
flags, creation semantics, approval rights — so the refresh can never leak
back into a write path or a permission decision.
"""

import json
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.spaces.errors import SpaceAccessDeniedError
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderApplicantAlreadyMemberError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderBizType,
    WorkOrderDecision,
    WorkOrderEventType,
    WorkOrderItemType,
    WorkOrderListItem,
    WorkOrderNotificationDraft,
    WorkOrderNotificationRecord,
    WorkOrderQueryType,
    WorkOrderReviewResult,
    WorkOrderStatus,
)
from agentclaw.community.plugin_api.staff_dept import (
    StaffDeptPlugin,
    StaffProfileInfo,
    StaffProfileLookupError,
)
from tests.community.core.work_orders.test_work_order_service import (
    NOW,
    _detail,
    _notification,
    _service,
    _space,
    _work_order,
)

_REFRESHABLE_SPACE_JOIN_CONTENT = json.dumps(
    {"text": "用户「200177」申请加入空间「Team」，请及时处理。"}, ensure_ascii=False
)
_REFRESHED_SPACE_JOIN_CONTENT = json.dumps(
    {"text": "用户「张三」申请加入空间「Team」，请及时处理。"}, ensure_ascii=False
)
_REFRESHABLE_BOT_CONTENT = "用户「200177」申请共同编辑 Bot「Editor Bot」，请及时处理。"


def _nickname_staff() -> MagicMock:
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="200177", nick_name="张三"
    )
    return staff_dept


def _space_join_item(*, can_approve: bool = True) -> WorkOrderListItem:
    return WorkOrderListItem(
        work_order=_work_order().model_copy(update={"applicant_user_id": "200177"}),
        notification=_notification().model_copy(
            update={"content": _REFRESHABLE_SPACE_JOIN_CONTENT}
        ),
        can_approve=can_approve,
    )


def _bot_item() -> WorkOrderListItem:
    return WorkOrderListItem(
        work_order=_work_order().model_copy(
            update={
                "biz_type": WorkOrderBizType.BOT_COLLABORATOR,
                "biz_id": "bot-17",
                "applicant_user_id": "200177",
                "biz_data": json.dumps({"bot_name": "Editor Bot"}),
            }
        ),
        notification=_applied_bot_notification(_REFRESHABLE_BOT_CONTENT),
        can_approve=False,
    )


def _applied_bot_notification(content: str) -> WorkOrderNotificationRecord:
    return _notification().model_copy(
        update={
            "event_type": WorkOrderEventType.BOT_COLLABORATOR_APPLIED,
            "biz_type": WorkOrderBizType.BOT_COLLABORATOR,
            "biz_id": "bot-17",
            "content": content,
        }
    )


def _refreshable_join_detail():
    return _detail().model_copy(
        update={
            "work_order": _work_order().model_copy(
                update={"applicant_user_id": "200177"}
            ),
            "content": _REFRESHABLE_SPACE_JOIN_CONTENT,
        }
    )


def test_read_refresh_makes_no_work_order_repository_writes() -> None:
    """Both read paths stay read-only on the work-order repository: only the
    listing and the detail read itself happen, never a write-back."""
    staff_dept = _nickname_staff()
    service, repository, spaces, _, _ = _service(staff_dept=staff_dept)
    spaces.get_space.return_value = _space()
    repository.list_items.return_value = (1, [_space_join_item()])
    repository.get_detail.return_value = _refreshable_join_detail()

    service.list_items(
        actor_id="owner-1",
        query_type=WorkOrderQueryType.PENDING_FOR_ME,
        item_type=WorkOrderItemType.APPROVAL,
        page_no=1,
        page_size=10,
    )
    service.get_detail(work_order_id=11, actor_id="owner-1")

    assert [call[0] for call in repository.method_calls] == [
        "list_items",
        "get_detail",
    ]


def test_refreshed_copies_change_content_only_and_rows_stay_verbatim() -> None:
    """A refreshed notification equals the stored row in every field except
    ``content``; the stored record itself (and its biz_data) never moves."""
    staff_dept = _nickname_staff()
    service, repository, spaces, _, _ = _service(staff_dept=staff_dept)
    spaces.get_space.return_value = _space()
    space_join_item = _space_join_item(can_approve=True)
    bot_item = _bot_item()
    stored_space_join = space_join_item.notification
    stored_bot = bot_item.notification
    repository.list_items.return_value = (2, [space_join_item, bot_item])

    _, items = service.list_items(
        actor_id="owner-1",
        query_type=WorkOrderQueryType.PENDING_FOR_ME,
        item_type=WorkOrderItemType.APPROVAL,
        page_no=1,
        page_size=10,
    )

    refreshed_space_join = items[0].notification
    refreshed_bot = items[1].notification
    assert refreshed_space_join is not stored_space_join
    assert refreshed_bot is not stored_bot
    assert refreshed_space_join.model_dump(exclude={"content"}) == (
        stored_space_join.model_dump(exclude={"content"})
    )
    assert refreshed_bot.model_dump(exclude={"content"}) == (
        stored_bot.model_dump(exclude={"content"})
    )
    # Persisted rows keep their exact original shape, biz_data included.
    assert stored_space_join.content == _REFRESHABLE_SPACE_JOIN_CONTENT
    assert stored_bot.content == _REFRESHABLE_BOT_CONTENT
    assert space_join_item.work_order.biz_data is None
    assert bot_item.work_order.biz_data == json.dumps({"bot_name": "Editor Bot"})
    # The work-order rows are shared, not copied: nothing on them changed.
    assert items[0].work_order is space_join_item.work_order
    assert items[1].work_order is bot_item.work_order


def test_read_flags_survive_the_presentation_refresh() -> None:
    """Message-status fields ride along verbatim into the refreshed copy."""
    staff_dept = _nickname_staff()
    service, repository, spaces, _, _ = _service(staff_dept=staff_dept)
    spaces.get_space.return_value = _space()
    stored = _notification().model_copy(
        update={
            "content": _REFRESHABLE_SPACE_JOIN_CONTENT,
            "is_read": True,
            "read_at": NOW,
        }
    )
    item = WorkOrderListItem(
        work_order=_work_order().model_copy(
            update={"applicant_user_id": "200177"}
        ),
        notification=stored,
        can_approve=False,
    )
    repository.list_items.return_value = (1, [item])

    _, items = service.list_items(
        actor_id="owner-1",
        query_type=WorkOrderQueryType.PROCESSED_BY_ME,
        item_type=WorkOrderItemType.APPROVAL,
        page_no=1,
        page_size=10,
    )

    refreshed = items[0].notification
    assert refreshed is not stored
    assert refreshed.is_read is True
    assert refreshed.read_at == NOW
    assert items[0].can_approve is False
    assert stored.is_read is True
    assert stored.read_at == NOW
    assert stored.content == _REFRESHABLE_SPACE_JOIN_CONTENT


def test_refresh_keeps_query_and_pagination_contract_verbatim() -> None:
    """Pagination, biz filters, and query shapes are forwarded unchanged even
    when refreshable rows ride along."""
    staff_dept = _nickname_staff()
    service, repository, spaces, _, _ = _service(staff_dept=staff_dept)
    spaces.get_space.return_value = _space()
    repository.list_items.return_value = (1, [_space_join_item()])

    service.list_items(
        actor_id="owner-1",
        query_type=WorkOrderQueryType.PENDING_FOR_ME,
        item_type=WorkOrderItemType.APPROVAL,
        biz_type="SPACE_JOIN",
        biz_id="7",
        page_no=3,
        page_size=5,
    )

    repository.list_items.assert_called_once_with(
        actor_id="owner-1",
        env="dev",
        query_type=WorkOrderQueryType.PENDING_FOR_ME,
        item_type=WorkOrderItemType.APPROVAL,
        biz_type="SPACE_JOIN",
        biz_id="7",
        offset=10,
        limit=5,
    )


def test_create_space_join_request_keeps_creation_semantics() -> None:
    """The nickname gain never widened the creation contract: the request
    delegates exactly once with the resolved name, and the read-refresh space
    lookup is never consulted."""
    staff_dept = _nickname_staff()
    service, repository, spaces, access, _ = _service(staff_dept=staff_dept)
    access.require_space.return_value = _space()
    spaces.get_member.return_value = None
    record = _work_order().model_copy(update={"applicant_user_id": "200177"})
    repository.create_space_join_request.return_value = record

    result = service.create_space_join_request(
        space_id=7, applicant_user_id="200177", reason="join"
    )

    assert result is record
    repository.create_space_join_request.assert_called_once_with(
        space_id=7,
        applicant_user_id="200177",
        applicant_name="张三",
        apply_reason="join",
        env="dev",
    )
    spaces.get_space.assert_not_called()


def test_create_space_join_request_falls_back_to_work_no_on_lookup_error() -> None:
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.side_effect = StaffProfileLookupError("down")
    service, repository, spaces, access, _ = _service(staff_dept=staff_dept)
    access.require_space.return_value = _space()
    spaces.get_member.return_value = None
    repository.create_space_join_request.return_value = _work_order()

    service.create_space_join_request(
        space_id=7, applicant_user_id="200177", reason=None
    )

    repository.create_space_join_request.assert_called_once_with(
        space_id=7,
        applicant_user_id="200177",
        applicant_name="200177",
        apply_reason=None,
        env="dev",
    )


def test_create_space_join_request_validation_precedes_resolution() -> None:
    staff_dept = _nickname_staff()
    service, repository, spaces, access, _ = _service(staff_dept=staff_dept)
    access.require_space.return_value = _space()
    spaces.get_member.return_value = object()

    with pytest.raises(WorkOrderApplicantAlreadyMemberError):
        service.create_space_join_request(
            space_id=7, applicant_user_id="200177", reason="join"
        )

    repository.create_space_join_request.assert_not_called()
    staff_dept.get_profile_by_work_no.assert_not_called()
    spaces.get_space.assert_not_called()


def test_refresh_never_touches_applicant_identity_in_list_items() -> None:
    """Whatever the refreshed copy shows, the row's applicant stays the
    persisted work number and the approver gate follows the repository."""
    staff_dept = _nickname_staff()
    service, repository, spaces, _, _ = _service(staff_dept=staff_dept)
    spaces.get_space.return_value = _space()
    item = _space_join_item(can_approve=True)
    repository.list_items.return_value = (1, [item])

    _, items = service.list_items(
        actor_id="owner-1",
        query_type=WorkOrderQueryType.PENDING_FOR_ME,
        item_type=WorkOrderItemType.APPROVAL,
        page_no=1,
        page_size=10,
    )

    assert items[0].work_order.applicant_user_id == "200177"
    assert items[0].can_approve is True
    assert items[0].work_order is item.work_order


def test_refresh_does_not_weaken_space_join_approval_rights() -> None:
    """A refreshed copy on the detail must not sway the owner-role judgment on
    the decision path: a non-owner stays denied, refreshed detail or not."""
    staff_dept = _nickname_staff()
    service, repository, spaces, access, _ = _service(staff_dept=staff_dept)
    detail = _detail().model_copy(
        update={
            "work_order": _work_order().model_copy(
                update={"applicant_user_id": "200177"}
            ),
            "content": _REFRESHABLE_SPACE_JOIN_CONTENT,
            "can_approve": True,
        }
    )
    repository.get_detail.return_value = detail
    access.require_space_owner.side_effect = SpaceAccessDeniedError("private")

    rendered = service.get_detail(work_order_id=11, actor_id="owner-1")
    assert rendered.content == _REFRESHED_SPACE_JOIN_CONTENT
    assert rendered.can_approve is True

    with pytest.raises(WorkOrderAccessDeniedError) as exc_info:
        service.reject(work_order_id=11, actor_id="member-9", review_remark="no")

    assert isinstance(exc_info.value.__cause__, SpaceAccessDeniedError)


def test_approval_write_contract_ignores_the_refreshed_copy() -> None:
    """The decision write keeps deriving its payload from the business fields;
    the refreshed display copy it carries never feeds the review payload."""
    staff_dept = _nickname_staff()
    service, repository, spaces, access, notifications = _service(
        staff_dept=staff_dept
    )
    repository.get_detail.return_value = _refreshable_join_detail()
    access.require_space_owner.return_value = (_space(), MagicMock())
    draft = WorkOrderNotificationDraft(
        recipient_user_id="200177",
        notification_category=NotificationCategory.NOTICE,
        event_type=WorkOrderEventType.SPACE_JOIN_REVIEWED,
        biz_type=WorkOrderBizType.SPACE_JOIN,
        biz_id="7",
        title="SPACE_JOIN_APPROVED",
        content={"text": "你加入空间「Team」的申请已通过。"},
    )
    notifications.build_space_join_review_result.return_value = draft
    expected = WorkOrderReviewResult(
        work_order_id=11,
        status=WorkOrderStatus.APPROVED,
        decision=WorkOrderDecision.APPROVED,
        reviewer_user_id="owner-1",
        review_remark=None,
        reviewed_at=NOW,
    )
    repository.review_space_join.return_value = expected

    result = service.approve(work_order_id=11, actor_id="owner-1", review_remark=None)

    assert result == expected
    build_kwargs = notifications.build_space_join_review_result.call_args.kwargs
    passed_detail = build_kwargs["detail"]
    assert build_kwargs["target_status"] == WorkOrderStatus.APPROVED
    # The notification builder sees the untouched business fields...
    assert passed_detail.space_name == "Team"
    assert passed_detail.space_id == 7
    assert passed_detail.work_order.applicant_user_id == "200177"
    # ...even though the carried display copy is the refreshed one.
    assert passed_detail.content == _REFRESHED_SPACE_JOIN_CONTENT
    repository.review_space_join.assert_called_once_with(
        work_order_id=11,
        reviewer_user_id="owner-1",
        review_remark=None,
        target_status=WorkOrderStatus.APPROVED,
        notification=draft,
        applicant_user_name="张三",
        env="dev",
    )
