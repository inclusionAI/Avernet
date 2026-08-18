"""Behavior tests for Space member management."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

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
from agentclaw.community.core.spaces.services.space_member_service import (
    SpaceMemberService,
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
    *, user_id: str = "member-1", role: SpaceRole = SpaceRole.MEMBER
) -> SpaceMemberRecord:
    now = datetime(2026, 8, 18, 10, 0, 0)
    return SpaceMemberRecord(
        id=2,
        space_id=7,
        user_id=user_id,
        role=role,
        env="test",
        created_by="owner-1",
        gmt_created=now,
        gmt_modified=now,
    )


def _service(*, space: SpaceRecord | None = None):
    repository = MagicMock()
    access = MagicMock()
    access.require_space_owner.return_value = (space or _space(), MagicMock())
    return SpaceMemberService(repository, access), repository, access


def test_list_members_checks_membership_and_normalizes_pagination() -> None:
    service, repository, access = _service()
    repository.list_members.return_value = (0, [])

    assert service.list_members(
        space_id=7,
        actor_id="member-1",
        keyword="  alice  ",
        page_no=3,
        page_size=20,
    ) == (0, [])

    access.require_space_member.assert_called_once_with(
        space_id=7, user_id="member-1"
    )
    repository.list_members.assert_called_once_with(
        space_id=7, env="dev", keyword="alice", offset=40, limit=20
    )


def test_list_members_turns_blank_keyword_into_none() -> None:
    service, repository, _ = _service()
    repository.list_members.return_value = (0, [])

    service.list_members(
        space_id=7, actor_id="member-1", keyword="  ", page_no=1, page_size=10
    )

    assert repository.list_members.call_args.kwargs["keyword"] is None


def test_add_member_propagates_requested_role_to_repository() -> None:
    service, repository, _ = _service()
    repository.get_member.return_value = None
    repository.add_member.return_value = _member(
        user_id="owner-2", role=SpaceRole.OWNER
    )

    record = service.add_member(
        space_id=7,
        actor_id="owner-1",
        user_id=" owner-2 ",
        role=SpaceRole.OWNER,
    )

    assert record.role is SpaceRole.OWNER
    repository.add_member.assert_called_once_with(
        space_id=7,
        user_id="owner-2",
        role=SpaceRole.OWNER,
        creator_id="owner-1",
        env="dev",
    )


@pytest.mark.parametrize("user_id", ["", "   "])
def test_add_member_rejects_blank_user_id(user_id: str) -> None:
    service, _, _ = _service()

    with pytest.raises(SpaceMemberInvalidError, match="user id is empty"):
        service.add_member(
            space_id=7,
            actor_id="owner-1",
            user_id=user_id,
            role=SpaceRole.MEMBER,
        )


def test_add_member_rejects_personal_space() -> None:
    service, _, _ = _service(space=_space(space_type=SpaceType.PERSONAL))

    with pytest.raises(PersonalSpaceInvariantError, match="cannot add members"):
        service.add_member(
            space_id=7,
            actor_id="owner-1",
            user_id="member-1",
            role=SpaceRole.MEMBER,
        )


def test_add_member_rejects_existing_member() -> None:
    service, repository, _ = _service()
    repository.get_member.return_value = _member()

    with pytest.raises(SpaceMemberAlreadyExistsError, match="already exists"):
        service.add_member(
            space_id=7,
            actor_id="owner-1",
            user_id="member-1",
            role=SpaceRole.MEMBER,
        )


def test_delete_member_returns_true_for_existing_non_creator() -> None:
    service, repository, _ = _service()
    repository.delete_member.return_value = True

    assert service.delete_member(
        space_id=7, actor_id="owner-1", user_id=" member-1 "
    ) is True
    repository.delete_member.assert_called_once_with(
        space_id=7, user_id="member-1", env="dev"
    )


def test_delete_member_rejects_creator() -> None:
    service, repository, _ = _service()

    with pytest.raises(SpaceCreatorInvariantError, match="cannot be removed"):
        service.delete_member(space_id=7, actor_id="owner-1", user_id="owner-1")

    repository.delete_member.assert_not_called()


def test_delete_member_reports_missing_target() -> None:
    service, repository, _ = _service()
    repository.delete_member.return_value = False

    with pytest.raises(SpaceMemberNotFoundError, match="not found"):
        service.delete_member(space_id=7, actor_id="owner-1", user_id="member-1")


def test_update_role_returns_summary_and_creator_flag() -> None:
    service, repository, _ = _service()
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
    service, _, _ = _service(space=_space(space_type=SpaceType.PERSONAL))

    with pytest.raises(PersonalSpaceInvariantError, match="immutable"):
        service.update_role(
            space_id=7,
            actor_id="owner-1",
            user_id="owner-1",
            role=SpaceRole.OWNER,
        )


def test_update_role_rejects_creator_demotion() -> None:
    service, repository, _ = _service()

    with pytest.raises(SpaceCreatorInvariantError, match="cannot be demoted"):
        service.update_role(
            space_id=7,
            actor_id="owner-1",
            user_id="owner-1",
            role=SpaceRole.MEMBER,
        )

    repository.update_member_role.assert_not_called()


def test_update_role_reports_missing_member() -> None:
    service, repository, _ = _service()
    repository.update_member_role.return_value = None

    with pytest.raises(SpaceMemberNotFoundError, match="not found"):
        service.update_role(
            space_id=7,
            actor_id="owner-1",
            user_id="member-1",
            role=SpaceRole.OWNER,
        )
