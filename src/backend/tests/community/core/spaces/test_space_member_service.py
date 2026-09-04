"""Behavior tests for Space member management."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.api.work_order_service import WorkOrderServiceProtocol
from agentclaw.community.core.spaces.errors import (
    PersonalSpaceInvariantError,
    SpaceCreatorInvariantError,
    SpaceMemberAlreadyExistsError,
    SpaceMemberInvalidError,
    SpaceMemberNotFoundError,
)
from agentclaw.community.core.spaces.models import (
    SpaceMemberRecord,
    SpaceRole,
    SpaceRecord,
    SpaceType,
)
from agentclaw.community.core.work_orders.models import NotificationCategory
from agentclaw.community.core.spaces.services.space_member_service import (
    SpaceMemberService,
)
from agentclaw.community.plugin_api.staff_dept import (
    StaffDeptPlugin,
    StaffProfileInfo,
    StaffProfileLookupError,
)


def _space(
    *, space_type: SpaceType = SpaceType.TEAM, created_by: str = "owner-1"
) -> SpaceRecord:
    now = datetime(2026, 8, 18, 10, 0, 0)
    return SpaceRecord(
        id=7,
        space_code="spc-7",
        space_type=space_type,
        name="Team",
        personal_owner_id=created_by if space_type is SpaceType.PERSONAL else None,
        env="test",
        created_by=created_by,
        updated_by=created_by,
        gmt_created=now,
        gmt_modified=now,
    )


def _member(
    *,
    user_id: str = "member-1",
    user_name: str | None = None,
    role: SpaceRole = SpaceRole.MEMBER,
) -> SpaceMemberRecord:
    now = datetime(2026, 8, 18, 10, 0, 0)
    return SpaceMemberRecord(
        id=2,
        space_id=7,
        user_id=user_id,
        user_name=user_name,
        role=role,
        env="test",
        created_by="owner-1",
        gmt_created=now,
        gmt_modified=now,
    )


def _service(
    *,
    space: SpaceRecord | None = None,
    staff_dept: StaffDeptPlugin | None = None,
):
    repository = MagicMock()
    access = MagicMock()
    access.require_space_owner.return_value = (space or _space(), MagicMock())
    work_orders = MagicMock(spec=WorkOrderServiceProtocol)
    if staff_dept is None:
        staff_dept = MagicMock(spec=StaffDeptPlugin)
        staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
            work_no="member-1", nick_name=None
        )
    return (
        SpaceMemberService(repository, access, staff_dept, work_orders),
        repository,
        access,
        staff_dept,
        work_orders,
    )


def test_list_members_checks_membership_and_normalizes_pagination() -> None:
    service, repository, access, _, _ = _service()
    repository.list_members.return_value = (0, [])

    assert service.list_members(
        space_id=7,
        actor_id="member-1",
        keyword="  alice  ",
        page_no=3,
        page_size=20,
    ) == (0, [])

    access.require_space_member.assert_called_once_with(space_id=7, user_id="member-1")
    repository.list_members.assert_called_once_with(
        space_id=7, env="dev", keyword="alice", offset=40, limit=20
    )


def test_list_members_turns_blank_keyword_into_none() -> None:
    service, repository, _, _, _ = _service()
    repository.list_members.return_value = (0, [])

    service.list_members(
        space_id=7, actor_id="member-1", keyword="  ", page_no=1, page_size=10
    )

    assert repository.list_members.call_args.kwargs["keyword"] is None


def test_add_member_resolves_nickname_and_propagates_requested_role() -> None:
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="owner-2", nick_name="  Owner Two  "
    )
    service, repository, _, _, _ = _service(staff_dept=staff_dept)
    repository.get_member.return_value = None
    repository.add_member.return_value = _member(
        user_id="owner-2", user_name="Owner Two", role=SpaceRole.OWNER
    )

    record = service.add_member(
        space_id=7,
        actor_id="owner-1",
        user_id=" owner-2 ",
        role=SpaceRole.OWNER,
    )

    assert record.role is SpaceRole.OWNER
    staff_dept.get_profile_by_work_no.assert_called_once_with(work_no="owner-2")
    repository.add_member.assert_called_once_with(
        space_id=7,
        user_id="owner-2",
        user_name="Owner Two",
        role=SpaceRole.OWNER,
        creator_id="owner-1",
        env="dev",
    )


@pytest.mark.parametrize("nickname", [None, "", "   "])
def test_add_member_falls_back_to_user_id_when_nickname_is_missing(
    nickname: str | None,
) -> None:
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="member-1", nick_name=nickname
    )
    service, repository, _, _, _ = _service(staff_dept=staff_dept)
    repository.get_member.return_value = None
    repository.add_member.return_value = _member()

    service.add_member(
        space_id=7,
        actor_id="owner-1",
        user_id="member-1",
        role=SpaceRole.MEMBER,
    )

    assert repository.add_member.call_args.kwargs["user_name"] == "member-1"


def test_add_member_falls_back_to_user_id_when_staff_lookup_fails() -> None:
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.side_effect = StaffProfileLookupError("down")
    service, repository, _, _, _ = _service(staff_dept=staff_dept)
    repository.get_member.return_value = None

    service.add_member(
        space_id=7,
        actor_id="owner-1",
        user_id="member-1",
        role=SpaceRole.MEMBER,
    )

    assert repository.add_member.call_args.kwargs["user_name"] == "member-1"


def test_add_member_truncates_staff_nickname_to_128_characters() -> None:
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="member-1", nick_name="花" * 129
    )
    service, repository, _, _, _ = _service(staff_dept=staff_dept)
    repository.get_member.return_value = None

    service.add_member(
        space_id=7,
        actor_id="owner-1",
        user_id="member-1",
        role=SpaceRole.MEMBER,
    )

    assert repository.add_member.call_args.kwargs["user_name"] == "花" * 128


@pytest.mark.parametrize("user_id", ["", "   "])
def test_add_member_rejects_blank_user_id(user_id: str) -> None:
    service, _, _, staff_dept, _ = _service()

    with pytest.raises(SpaceMemberInvalidError, match="user id is empty"):
        service.add_member(
            space_id=7,
            actor_id="owner-1",
            user_id=user_id,
            role=SpaceRole.MEMBER,
        )

    staff_dept.get_profile_by_work_no.assert_not_called()


def test_add_member_rejects_personal_space() -> None:
    service, _, _, staff_dept, _ = _service(
        space=_space(space_type=SpaceType.PERSONAL)
    )

    with pytest.raises(PersonalSpaceInvariantError, match="cannot add members"):
        service.add_member(
            space_id=7,
            actor_id="owner-1",
            user_id="member-1",
            role=SpaceRole.MEMBER,
        )

    staff_dept.get_profile_by_work_no.assert_not_called()


def test_add_member_rejects_existing_member() -> None:
    service, repository, _, staff_dept, _ = _service()
    repository.get_member.return_value = _member()

    with pytest.raises(SpaceMemberAlreadyExistsError, match="already exists"):
        service.add_member(
            space_id=7,
            actor_id="owner-1",
            user_id="member-1",
            role=SpaceRole.MEMBER,
        )

    staff_dept.get_profile_by_work_no.assert_not_called()


def test_delete_member_returns_true_for_existing_non_creator() -> None:
    service, repository, _, _, work_orders = _service()
    repository.delete_member.return_value = True

    assert (
        service.delete_member(space_id=7, actor_id="owner-1", user_id=" member-1 ")
        is True
    )
    repository.delete_member.assert_called_once_with(
        space_id=7, user_id="member-1", env="dev"
    )
    work_orders.create_work_order_event.assert_called_once_with(
        event_category=NotificationCategory.NOTICE,
        biz_type="SPACE",
        biz_id="7",
        event_type="SPACE_MEMBER_REMOVED",
        applicant_user_id=None,
        approver_user_ids=[],
        recipient_user_ids=["member-1"],
        title="你已被移出空间",
        content={"text": "你已被移出空间「Team」。"},
        apply_reason=None,
        biz_data=None,
        actor_id="owner-1",
    )


def test_delete_member_does_not_notify_when_target_is_missing() -> None:
    service, repository, _, _, work_orders = _service()
    repository.delete_member.return_value = False

    with pytest.raises(SpaceMemberNotFoundError, match="not found"):
        service.delete_member(space_id=7, actor_id="owner-1", user_id="member-1")

    work_orders.create_work_order_event.assert_not_called()


def test_delete_member_propagates_notification_failure() -> None:
    service, repository, _, _, work_orders = _service()
    repository.delete_member.return_value = True
    work_orders.create_work_order_event.side_effect = RuntimeError("notification failed")

    with pytest.raises(RuntimeError, match="notification failed"):
        service.delete_member(space_id=7, actor_id="owner-1", user_id="member-1")

    work_orders.create_work_order_event.assert_called_once()


def test_delete_member_rejects_creator() -> None:
    service, repository, _, _, _ = _service()

    with pytest.raises(SpaceCreatorInvariantError, match="cannot be removed"):
        service.delete_member(space_id=7, actor_id="owner-1", user_id="owner-1")

    repository.delete_member.assert_not_called()


def test_delete_member_reports_missing_target() -> None:
    service, repository, _, _, _ = _service()
    repository.delete_member.return_value = False

    with pytest.raises(SpaceMemberNotFoundError, match="not found"):
        service.delete_member(space_id=7, actor_id="owner-1", user_id="member-1")


def test_update_role_returns_summary_and_creator_flag() -> None:
    service, repository, _, _, _ = _service()
    repository.update_member_role.return_value = _member(
        user_id="owner-1", role=SpaceRole.OWNER
    )

    summary = service.update_role(
        space_id=7,
        actor_id="owner-1",
        user_id=" owner-1 ",
        role=SpaceRole.OWNER,
    )

    assert summary.is_creator is True
    assert summary.member.role is SpaceRole.OWNER


def test_update_role_rejects_personal_space() -> None:
    service, _, _, _, _ = _service(space=_space(space_type=SpaceType.PERSONAL))

    with pytest.raises(PersonalSpaceInvariantError, match="immutable"):
        service.update_role(
            space_id=7,
            actor_id="owner-1",
            user_id="owner-1",
            role=SpaceRole.OWNER,
        )


def test_update_role_rejects_creator_demotion() -> None:
    service, repository, _, _, _ = _service()

    with pytest.raises(SpaceCreatorInvariantError, match="cannot be demoted"):
        service.update_role(
            space_id=7,
            actor_id="owner-1",
            user_id="owner-1",
            role=SpaceRole.MEMBER,
        )

    repository.update_member_role.assert_not_called()


def test_update_role_reports_missing_member() -> None:
    service, repository, _, _, _ = _service()
    repository.update_member_role.return_value = None

    with pytest.raises(SpaceMemberNotFoundError, match="not found"):
        service.update_role(
            space_id=7,
            actor_id="owner-1",
            user_id="member-1",
            role=SpaceRole.OWNER,
        )
