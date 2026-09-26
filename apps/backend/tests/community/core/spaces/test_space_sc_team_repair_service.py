"""Unit tests for the lookup-only historical SC Team binding repair."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, call

import pytest

from agentclaw.community.core.spaces.errors import (
    SpaceNotFoundError,
    SpaceScTeamBindingNotFoundError,
    SpaceScTeamRepairConflictError,
    SpaceScTeamRepairNotApplicableError,
)
from agentclaw.community.core.spaces.models import (
    SpaceRecord,
    SpaceScTeamRepairStatus,
    SpaceType,
)
from agentclaw.community.core.spaces.services.space_service import SpaceService
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterTeamQueryError,
    SkillCenterTeamQueryRequest,
    SkillCenterTeamQueryResult,
)


def _space(
    *, space_type: SpaceType = SpaceType.TEAM, sc_team_id: str | None = None
) -> SpaceRecord:
    now = datetime(2026, 8, 20, 10, 0)
    return SpaceRecord(
        id=7,
        space_code="spc-repair-test",
        space_type=space_type,
        name="Historical Team",
        personal_owner_id="owner-1" if space_type is SpaceType.PERSONAL else None,
        sc_team_id=sc_team_id,
        env="dev",
        created_by="owner-1",
        updated_by="owner-1",
        gmt_created=now,
        gmt_modified=now,
    )


def test_repair_sc_team_binding_fills_mapping_found_by_external_reference() -> None:
    repository = MagicMock()
    repository.get_space.return_value = _space()
    repository.backfill_sc_team_id.return_value = True
    skill_center = MagicMock()
    skill_center.get_team_by_ref_source.return_value = SkillCenterTeamQueryResult(
        team_id="sc-team-7"
    )

    result = SpaceService(repository, skill_center, MagicMock()).repair_sc_team_binding(space_id=7)

    assert result.space_id == 7
    assert result.status is SpaceScTeamRepairStatus.REPAIRED
    assert result.sc_team_id == "sc-team-7"
    repository.get_space.assert_called_once_with(space_id=7, env="dev")
    skill_center.get_team_by_ref_source.assert_called_once_with(
        SkillCenterTeamQueryRequest(source="OCB", ref_source_id="7")
    )
    repository.backfill_sc_team_id.assert_called_once_with(
        space_id=7, env="dev", sc_team_id="sc-team-7"
    )
    skill_center.create_team.assert_not_called()


def test_repair_sc_team_binding_is_idempotent_for_existing_binding() -> None:
    repository = MagicMock()
    repository.get_space.return_value = _space(sc_team_id="existing-team")
    skill_center = MagicMock()

    result = SpaceService(repository, skill_center, MagicMock()).repair_sc_team_binding(space_id=7)

    assert result.status is SpaceScTeamRepairStatus.ALREADY_BOUND
    assert result.sc_team_id == "existing-team"
    skill_center.get_team_by_ref_source.assert_not_called()
    skill_center.create_team.assert_not_called()
    repository.backfill_sc_team_id.assert_not_called()


def test_repair_sc_team_binding_rejects_missing_space_before_sc_lookup() -> None:
    repository = MagicMock()
    repository.get_space.return_value = None
    skill_center = MagicMock()

    with pytest.raises(SpaceNotFoundError, match="space 7 not found"):
        SpaceService(repository, skill_center, MagicMock()).repair_sc_team_binding(space_id=7)

    skill_center.get_team_by_ref_source.assert_not_called()
    repository.backfill_sc_team_id.assert_not_called()


def test_repair_sc_team_binding_rejects_personal_space_before_sc_lookup() -> None:
    repository = MagicMock()
    repository.get_space.return_value = _space(space_type=SpaceType.PERSONAL)
    skill_center = MagicMock()

    with pytest.raises(SpaceScTeamRepairNotApplicableError, match="TEAM spaces"):
        SpaceService(repository, skill_center, MagicMock()).repair_sc_team_binding(space_id=7)

    skill_center.get_team_by_ref_source.assert_not_called()
    skill_center.create_team.assert_not_called()
    repository.backfill_sc_team_id.assert_not_called()


def test_repair_sc_team_binding_never_creates_when_sc_mapping_is_missing() -> None:
    repository = MagicMock()
    repository.get_space.return_value = _space()
    skill_center = MagicMock()
    skill_center.get_team_by_ref_source.return_value = None

    with pytest.raises(SpaceScTeamBindingNotFoundError, match="was not found"):
        SpaceService(repository, skill_center, MagicMock()).repair_sc_team_binding(space_id=7)

    skill_center.create_team.assert_not_called()
    repository.backfill_sc_team_id.assert_not_called()


def test_repair_sc_team_binding_propagates_sc_query_failure() -> None:
    repository = MagicMock()
    repository.get_space.return_value = _space()
    skill_center = MagicMock()
    skill_center.get_team_by_ref_source.side_effect = SkillCenterTeamQueryError(
        "SC unavailable"
    )

    with pytest.raises(SkillCenterTeamQueryError, match="SC unavailable"):
        SpaceService(repository, skill_center, MagicMock()).repair_sc_team_binding(space_id=7)

    repository.backfill_sc_team_id.assert_not_called()


def test_repair_sc_team_binding_returns_concurrent_winner_without_overwrite() -> None:
    repository = MagicMock()
    repository.get_space.side_effect = [
        _space(),
        _space(sc_team_id="concurrent-team"),
    ]
    repository.backfill_sc_team_id.return_value = False
    skill_center = MagicMock()
    skill_center.get_team_by_ref_source.return_value = SkillCenterTeamQueryResult(
        team_id="resolved-team"
    )

    result = SpaceService(repository, skill_center, MagicMock()).repair_sc_team_binding(space_id=7)

    assert result.status is SpaceScTeamRepairStatus.ALREADY_BOUND
    assert result.sc_team_id == "concurrent-team"
    assert repository.get_space.call_args_list == [
        call(space_id=7, env="dev"),
        call(space_id=7, env="dev"),
    ]


@pytest.mark.parametrize("current", [None, _space()])
def test_repair_sc_team_binding_reports_unresolved_conditional_update(current) -> None:
    repository = MagicMock()
    repository.get_space.side_effect = [_space(), current]
    repository.backfill_sc_team_id.return_value = False
    skill_center = MagicMock()
    skill_center.get_team_by_ref_source.return_value = SkillCenterTeamQueryResult(
        team_id="resolved-team"
    )

    with pytest.raises(SpaceScTeamRepairConflictError, match="while repair"):
        SpaceService(repository, skill_center, MagicMock()).repair_sc_team_binding(space_id=7)
