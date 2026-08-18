"""Focused behavior tests for team Space creation and SC synchronization."""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.repository.implementations.spaces.space import (
    SpaceRepository,
)
from agentclaw.community.core.spaces.models import SpaceRecord, SpaceType
from agentclaw.community.core.spaces.services.space_service import SpaceService
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterTeamCreateError,
    SkillCenterTeamCreateRequest,
    SkillCenterTeamCreateResult,
)


def _space() -> SpaceRecord:
    now = datetime(2026, 8, 18, 10, 0, 0)
    return SpaceRecord(
        id=7,
        space_code="spc-0123456789abcdef0123",
        space_type=SpaceType.TEAM,
        name="Demo Team",
        personal_owner_id=None,
        env="dev",
        created_by="owner-1",
        updated_by="owner-1",
        gmt_created=now,
        gmt_modified=now,
    )


class _TransactionRepository:
    def __init__(self, record: SpaceRecord) -> None:
        self.record = record
        self.committed = False
        self.rolled_back = False
        self.arguments: dict[str, str] = {}

    @contextmanager
    def create_team_transaction(self, *, name: str, creator_id: str, env: str):
        self.arguments = {"name": name, "creator_id": creator_id, "env": env}
        try:
            yield self.record
        except Exception:
            self.rolled_back = True
            raise
        else:
            self.committed = True


def test_create_team_pushes_to_sc_before_transaction_commit() -> None:
    repository = _TransactionRepository(_space())
    skill_center = MagicMock()
    skill_center.create_team.return_value = SkillCenterTeamCreateResult(
        team_id="sc-team-9001"
    )
    service = SpaceService(repository, skill_center)

    record = service.create_team(name="  Demo Team  ", creator_id="owner-1")

    assert record == repository.record
    assert repository.arguments == {
        "name": "Demo Team",
        "creator_id": "owner-1",
        "env": "dev",
    }
    skill_center.create_team.assert_called_once_with(
        SkillCenterTeamCreateRequest(
            team_code="spc-0123456789abcdef0123",
            team_name="Demo Team",
            ref_source_id="7",
        )
    )
    assert record.sc_team_id == "sc-team-9001"
    assert repository.committed is True
    assert repository.rolled_back is False


def test_create_team_rolls_back_when_sc_creation_fails() -> None:
    repository = _TransactionRepository(_space())
    skill_center = MagicMock()
    skill_center.create_team.side_effect = SkillCenterTeamCreateError("SC failed")
    service = SpaceService(repository, skill_center)

    with pytest.raises(SkillCenterTeamCreateError, match="SC failed"):
        service.create_team(name="Demo Team", creator_id="owner-1")

    assert repository.committed is False
    assert repository.rolled_back is True


def test_generated_space_code_matches_sc_format() -> None:
    code = SpaceRepository._new_code()

    assert len(code) == 24
    assert re.fullmatch(r"spc-[0-9a-f]{20}", code)


@pytest.mark.parametrize("name", ["", "   ", "x" * 129])
def test_create_team_rejects_invalid_name(name: str) -> None:
    repository = MagicMock()
    skill_center = MagicMock()
    service = SpaceService(repository, skill_center)

    from agentclaw.community.core.spaces.errors import SpaceNameInvalidError

    with pytest.raises(SpaceNameInvalidError, match="1-128"):
        service.create_team(name=name, creator_id="owner-1")

    repository.create_team_transaction.assert_not_called()
    skill_center.create_team.assert_not_called()


def test_initialize_personal_delegates_to_repository() -> None:
    repository = MagicMock()
    repository.initialize_personal.return_value = (_space(), True)
    service = SpaceService(repository, MagicMock())

    assert service.initialize_personal(user_id="owner-1") == (_space(), True)
    repository.initialize_personal.assert_called_once_with(user_id="owner-1", env="dev")


def test_list_spaces_normalizes_filters_and_pagination() -> None:
    repository = MagicMock()
    repository.list_spaces.return_value = (0, [])
    service = SpaceService(repository, MagicMock())

    assert service.list_spaces(
        user_id="owner-1",
        keyword="  Demo  ",
        space_type=SpaceType.TEAM,
        page_no=2,
        page_size=25,
    ) == (0, [])
    repository.list_spaces.assert_called_once_with(
        user_id="owner-1",
        env="dev",
        keyword="Demo",
        space_type="TEAM",
        offset=25,
        limit=25,
    )


def test_list_spaces_turns_blank_optional_filters_into_none() -> None:
    repository = MagicMock()
    repository.list_spaces.return_value = (0, [])
    service = SpaceService(repository, MagicMock())

    service.list_spaces(
        user_id="owner-1",
        keyword="  ",
        space_type=None,
        page_no=1,
        page_size=20,
    )

    assert repository.list_spaces.call_args.kwargs["keyword"] is None
    assert repository.list_spaces.call_args.kwargs["space_type"] is None
