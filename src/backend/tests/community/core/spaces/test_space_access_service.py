"""Unit tests for centralized Space authorization."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.spaces.errors import (
    SpaceAccessDeniedError,
    SpaceNotFoundError,
)
from agentclaw.community.core.spaces.models import (
    SpaceMemberRecord,
    SpaceRecord,
    SpaceRole,
    SpaceType,
)
from agentclaw.community.core.spaces.services.space_access_service import (
    SpaceAccessService,
)


def _space(*, created_by: str = "owner-1") -> SpaceRecord:
    now = datetime(2026, 8, 18, 10, 0, 0)
    return SpaceRecord(
        id=7,
        space_code="spc-7",
        space_type=SpaceType.TEAM,
        name="Team",
        personal_owner_id=None,
        env="test",
        created_by=created_by,
        updated_by=created_by,
        gmt_created=now,
        gmt_modified=now,
    )


def _member(*, role: SpaceRole = SpaceRole.MEMBER) -> SpaceMemberRecord:
    now = datetime(2026, 8, 18, 10, 0, 0)
    return SpaceMemberRecord(
        id=2,
        space_id=7,
        user_id="member-1",
        role=role,
        env="test",
        created_by="owner-1",
        gmt_created=now,
        gmt_modified=now,
    )


def test_require_space_returns_existing_space() -> None:
    repository = MagicMock()
    repository.get_space.return_value = _space()

    result = SpaceAccessService(repository).require_space(space_id=7)

    assert result.id == 7
    repository.get_space.assert_called_once_with(space_id=7, env="dev")


def test_require_space_rejects_missing_space() -> None:
    repository = MagicMock()
    repository.get_space.return_value = None

    with pytest.raises(SpaceNotFoundError, match="space not found"):
        SpaceAccessService(repository).require_space(space_id=7)


def test_require_space_reference_accepts_numeric_team_prefix_and_space_code() -> None:
    repository = MagicMock()
    repository.get_space.side_effect = [_space(), _space()]
    repository.get_space_by_code.return_value = _space()
    service = SpaceAccessService(repository)

    assert service.require_space_reference(space_ref="7").id == 7
    assert service.require_space_reference(space_ref="team:7").id == 7
    assert service.require_space_reference(space_ref="spc-7").id == 7
    repository.get_space_by_code.assert_called_once_with(space_code="spc-7", env="dev")


def test_require_space_reference_rejects_team_prefix_for_personal_space() -> None:
    repository = MagicMock()
    repository.get_space.return_value = _space().model_copy(
        update={
            "space_type": SpaceType.PERSONAL,
            "personal_owner_id": "owner-1",
        }
    )
    service = SpaceAccessService(repository)

    with pytest.raises(SpaceNotFoundError, match="space not found"):
        service.require_space_reference(space_ref="team:7")


def test_get_space_role_returns_role_or_none() -> None:
    repository = MagicMock()
    repository.get_member.side_effect = [_member(role=SpaceRole.OWNER), None]
    service = SpaceAccessService(repository)

    assert service.get_space_role(space_id=7, user_id="owner-1") is SpaceRole.OWNER
    assert service.get_space_role(space_id=7, user_id="stranger") is None


def test_require_space_member_returns_space_and_membership() -> None:
    repository = MagicMock()
    space = _space()
    member = _member()
    repository.get_space.return_value = space
    repository.get_member.return_value = member

    assert SpaceAccessService(repository).require_space_member(
        space_id=7, user_id="member-1"
    ) == (space, member)


def test_require_space_member_rejects_non_member() -> None:
    repository = MagicMock()
    repository.get_space.return_value = _space()
    repository.get_member.return_value = None

    with pytest.raises(SpaceAccessDeniedError, match="membership required"):
        SpaceAccessService(repository).require_space_member(
            space_id=7, user_id="stranger"
        )


def test_require_space_owner_accepts_owner_and_rejects_member() -> None:
    repository = MagicMock()
    repository.get_space.return_value = _space()
    repository.get_member.side_effect = [
        _member(role=SpaceRole.OWNER),
        _member(role=SpaceRole.MEMBER),
    ]
    service = SpaceAccessService(repository)

    _, owner = service.require_space_owner(space_id=7, user_id="owner-1")
    assert owner.role is SpaceRole.OWNER
    with pytest.raises(SpaceAccessDeniedError, match="owner role required"):
        service.require_space_owner(space_id=7, user_id="member-1")


def test_require_space_creator_accepts_creator_and_rejects_other_user() -> None:
    repository = MagicMock()
    repository.get_space.return_value = _space(created_by="owner-1")
    service = SpaceAccessService(repository)

    assert service.require_space_creator(space_id=7, user_id="owner-1").id == 7
    with pytest.raises(SpaceAccessDeniedError, match="creator required"):
        service.require_space_creator(space_id=7, user_id="owner-2")
