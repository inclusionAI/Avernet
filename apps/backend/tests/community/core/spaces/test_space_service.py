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
from agentclaw.community.core.spaces.errors import SpaceAlreadyExistsError
from agentclaw.community.core.spaces.models import SpaceListScope, SpaceRecord, SpaceType
from agentclaw.community.core.spaces.services.space_service import SpaceService
from agentclaw.community.plugin_api.staff_dept import (
    StaffProfileInfo,
    StaffProfileLookupError,
)
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterTeamCreateError,
    SkillCenterTeamCreateRequest,
    SkillCenterTeamCreateResult,
    SkillCenterTeamQueryRequest,
    SkillCenterTeamQueryResult,
)


def _make_service(repository, skill_center, staff_dept=None):
    if staff_dept is None:
        staff_dept = MagicMock()
        staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
            work_no="owner-1", nick_name=None
        )
    if isinstance(repository, MagicMock):
        repository.get_team_space_by_name.return_value = None
    return SpaceService(repository, skill_center, staff_dept)


def _space(
    *,
    space_type: SpaceType = SpaceType.TEAM,
    sc_team_id: str | None = None,
) -> SpaceRecord:
    now = datetime(2026, 8, 18, 10, 0, 0)
    return SpaceRecord(
        id=7,
        space_code="spc-0123456789abcdef0123",
        space_type=space_type,
        name="Demo Team" if space_type is SpaceType.TEAM else "个人空间",
        personal_owner_id=("owner-1" if space_type is SpaceType.PERSONAL else None),
        sc_team_id=sc_team_id,
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

    def get_team_space_by_name(
        self, *, creator_id: str, name: str, env: str
    ) -> SpaceRecord | None:
        return None

    @contextmanager
    def create_team_transaction(
        self, *, name: str, creator_id: str, creator_user_name: str | None, env: str
    ):
        self.arguments = {"name": name, "creator_id": creator_id, "creator_user_name": creator_user_name, "env": env}
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
    service = _make_service(repository, skill_center)

    record = service.create_team(name="  Demo Team  ", creator_id="owner-1")

    assert record == repository.record
    assert repository.arguments == {
        "name": "Demo Team",
        "creator_id": "owner-1",
        "creator_user_name": None,
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


def test_create_team_rejects_same_creator_and_name() -> None:
    repository = MagicMock()
    skill_center = MagicMock()
    service = _make_service(repository, skill_center)
    repository.get_team_space_by_name.return_value = _space()

    with pytest.raises(SpaceAlreadyExistsError, match="same name"):
        service.create_team(name="  Demo Team  ", creator_id="owner-1")

    repository.get_team_space_by_name.assert_called_once_with(
        creator_id="owner-1", name="Demo Team", env="dev"
    )
    repository.create_team_transaction.assert_not_called()
    skill_center.create_team.assert_not_called()


def test_create_team_allows_same_name_for_different_creator() -> None:
    repository = _TransactionRepository(_space())
    repository.get_team_space_by_name = MagicMock(return_value=None)
    skill_center = MagicMock()
    skill_center.create_team.return_value = SkillCenterTeamCreateResult(
        team_id="sc-team-9001"
    )
    service = _make_service(repository, skill_center)

    service.create_team(name="Demo Team", creator_id="owner-2")

    repository.get_team_space_by_name.assert_called_once_with(
        creator_id="owner-2", name="Demo Team", env="dev"
    )
    assert repository.arguments["creator_id"] == "owner-2"


def test_create_team_rolls_back_when_sc_creation_fails() -> None:
    repository = _TransactionRepository(_space())
    skill_center = MagicMock()
    skill_center.create_team.side_effect = SkillCenterTeamCreateError("SC failed")
    service = _make_service(repository, skill_center)

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
    service = _make_service(repository, skill_center)

    from agentclaw.community.core.spaces.errors import SpaceNameInvalidError

    with pytest.raises(SpaceNameInvalidError, match="1-128"):
        service.create_team(name=name, creator_id="owner-1")

    repository.create_team_transaction.assert_not_called()
    skill_center.create_team.assert_not_called()


def test_initialize_personal_creates_sc_team_before_local_commit() -> None:
    personal = _space(space_type=SpaceType.PERSONAL)
    state = {"committed": False, "rolled_back": False}

    @contextmanager
    def create_transaction(*, user_id: str, creator_user_name: str | None, env: str):
        assert (user_id, creator_user_name, env) == ("owner-1", None, "dev")
        try:
            yield personal
        except Exception:
            state["rolled_back"] = True
            raise
        else:
            state["committed"] = True

    repository = MagicMock()
    repository.get_personal_space.return_value = None
    repository.create_personal_transaction.side_effect = create_transaction
    skill_center = MagicMock()
    skill_center.create_team.return_value = SkillCenterTeamCreateResult(
        team_id="sc-personal-7"
    )
    service = _make_service(repository, skill_center)

    record, created = service.initialize_personal(user_id="owner-1")

    assert created is True
    assert record.sc_team_id == "sc-personal-7"
    assert state == {"committed": True, "rolled_back": False}
    skill_center.create_team.assert_called_once_with(
        SkillCenterTeamCreateRequest(
            team_code=personal.space_code,
            team_name="个人空间",
            ref_source_id="7",
        )
    )
    skill_center.get_team_by_ref_source.assert_not_called()


def test_initialize_personal_rolls_back_when_sc_creation_fails() -> None:
    personal = _space(space_type=SpaceType.PERSONAL)
    state = {"committed": False, "rolled_back": False}

    @contextmanager
    def create_transaction(*, user_id: str, creator_user_name: str | None, env: str):
        try:
            yield personal
        except Exception:
            state["rolled_back"] = True
            raise
        else:
            state["committed"] = True

    repository = MagicMock()
    repository.get_personal_space.return_value = None
    repository.create_personal_transaction.side_effect = create_transaction
    skill_center = MagicMock()
    skill_center.create_team.side_effect = SkillCenterTeamCreateError("SC failed")

    with pytest.raises(SkillCenterTeamCreateError, match="SC failed"):
        _make_service(repository, skill_center).initialize_personal(user_id="owner-1")

    assert state == {"committed": False, "rolled_back": True}


def test_initialize_personal_returns_existing_sc_binding_without_external_calls() -> (
    None
):
    existing = _space(space_type=SpaceType.PERSONAL, sc_team_id="sc-personal-existing")
    repository = MagicMock()
    repository.get_personal_space.return_value = existing
    skill_center = MagicMock()

    assert _make_service(repository, skill_center).initialize_personal(
        user_id="owner-1"
    ) == (existing, False)

    repository.create_personal_transaction.assert_not_called()
    repository.personal_sc_team_binding_transaction.assert_not_called()
    skill_center.create_team.assert_not_called()
    skill_center.get_team_by_ref_source.assert_not_called()


@pytest.mark.parametrize("mapping_found", [True, False])
def test_initialize_personal_repairs_missing_existing_sc_binding(
    mapping_found: bool,
) -> None:
    existing = _space(space_type=SpaceType.PERSONAL)

    @contextmanager
    def binding_transaction(*, space_id: int, env: str):
        assert (space_id, env) == (7, "dev")
        yield existing

    repository = MagicMock()
    repository.get_personal_space.return_value = existing
    repository.personal_sc_team_binding_transaction.side_effect = binding_transaction
    skill_center = MagicMock()
    skill_center.get_team_by_ref_source.return_value = (
        SkillCenterTeamQueryResult(team_id="sc-found") if mapping_found else None
    )
    skill_center.create_team.return_value = SkillCenterTeamCreateResult(
        team_id="sc-created"
    )

    record, created = _make_service(repository, skill_center).initialize_personal(
        user_id="owner-1"
    )

    assert created is False
    assert record.sc_team_id == ("sc-found" if mapping_found else "sc-created")
    skill_center.get_team_by_ref_source.assert_called_once_with(
        SkillCenterTeamQueryRequest(source="OCB", ref_source_id="7")
    )
    if mapping_found:
        skill_center.create_team.assert_not_called()
    else:
        skill_center.create_team.assert_called_once_with(
            SkillCenterTeamCreateRequest(
                team_code=existing.space_code,
                team_name="个人空间",
                ref_source_id="7",
            )
        )


def test_initialize_personal_recovers_from_concurrent_local_creation() -> None:
    existing = _space(space_type=SpaceType.PERSONAL, sc_team_id="sc-concurrent-winner")

    @contextmanager
    def conflicting_transaction(*, user_id: str, creator_user_name: str | None, env: str):
        raise SpaceAlreadyExistsError("personal space already exists")
        yield  # pragma: no cover - contextmanager requires a generator

    repository = MagicMock()
    repository.get_personal_space.side_effect = [None, existing]
    repository.create_personal_transaction.side_effect = conflicting_transaction
    skill_center = MagicMock()

    assert _make_service(repository, skill_center).initialize_personal(
        user_id="owner-1"
    ) == (existing, False)

    skill_center.create_team.assert_not_called()
    skill_center.get_team_by_ref_source.assert_not_called()


def test_initialize_personal_reraises_concurrent_creation_without_winner() -> None:
    @contextmanager
    def conflicting_transaction(*, user_id: str, creator_user_name: str | None, env: str):
        raise SpaceAlreadyExistsError("personal space already exists")
        yield  # pragma: no cover - contextmanager requires a generator

    repository = MagicMock()
    repository.get_personal_space.side_effect = [None, None]
    repository.create_personal_transaction.side_effect = conflicting_transaction
    skill_center = MagicMock()

    with pytest.raises(SpaceAlreadyExistsError, match="personal space already exists"):
        _make_service(repository, skill_center).initialize_personal(user_id="owner-1")

    skill_center.create_team.assert_not_called()
    skill_center.get_team_by_ref_source.assert_not_called()


def test_initialize_personal_uses_binding_completed_by_concurrent_request() -> None:
    stale = _space(space_type=SpaceType.PERSONAL)
    current = _space(space_type=SpaceType.PERSONAL, sc_team_id="sc-concurrent-binding")

    @contextmanager
    def binding_transaction(*, space_id: int, env: str):
        assert (space_id, env) == (7, "dev")
        yield current

    repository = MagicMock()
    repository.get_personal_space.return_value = stale
    repository.personal_sc_team_binding_transaction.side_effect = binding_transaction
    skill_center = MagicMock()

    assert _make_service(repository, skill_center).initialize_personal(
        user_id="owner-1"
    ) == (current, False)

    skill_center.create_team.assert_not_called()
    skill_center.get_team_by_ref_source.assert_not_called()


def test_list_spaces_normalizes_filters_and_pagination() -> None:
    repository = MagicMock()
    repository.list_spaces.return_value = (0, [])
    service = _make_service(repository, MagicMock())

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
        scope=SpaceListScope.ALL,
    )


def test_list_spaces_forwards_accessible_scope() -> None:
    repository = MagicMock()
    repository.list_spaces.return_value = (0, [])
    service = _make_service(repository, MagicMock())

    service.list_spaces(
        user_id="owner-1",
        keyword=None,
        space_type=None,
        page_no=1,
        page_size=20,
        scope=SpaceListScope.ACCESSIBLE,
    )

    assert repository.list_spaces.call_args.kwargs["scope"] is SpaceListScope.ACCESSIBLE


def test_list_spaces_turns_blank_optional_filters_into_none() -> None:
    repository = MagicMock()
    repository.list_spaces.return_value = (0, [])
    service = _make_service(repository, MagicMock())

    service.list_spaces(
        user_id="owner-1",
        keyword="  ",
        space_type=None,
        page_no=1,
        page_size=20,
    )

    assert repository.list_spaces.call_args.kwargs["keyword"] is None
    assert repository.list_spaces.call_args.kwargs["space_type"] is None


def test_batch_query_personal_deduplicates_and_preserves_first_occurrence() -> None:
    repository = MagicMock()
    repository.batch_query_personal.return_value = []
    service = _make_service(repository, MagicMock())

    assert service.batch_query_personal(user_ids=[" user-2 ", "user-1", "user-2"]) == []
    repository.batch_query_personal.assert_called_once_with(
        user_ids=["user-2", "user-1"], env="dev"
    )


@pytest.mark.parametrize("user_ids", [[], ["  "], [str(index) for index in range(501)]])
def test_batch_query_personal_rejects_invalid_ids(user_ids: list[str]) -> None:
    repository = MagicMock()
    service = _make_service(repository, MagicMock())

    with pytest.raises(ValueError, match="user_id"):
        service.batch_query_personal(user_ids=user_ids)

    repository.batch_query_personal.assert_not_called()


def test_initialize_personal_passes_creator_user_name_to_repository() -> None:
    repository = MagicMock()
    repository.get_personal_space.return_value = None
    personal = _space(space_type=SpaceType.PERSONAL)

    @contextmanager
    def create_transaction(*, user_id: str, creator_user_name: str | None, env: str):
        assert (user_id, creator_user_name, env) == ("owner-1", "Creator", "dev")
        yield personal

    repository.create_personal_transaction.side_effect = create_transaction
    skill_center = MagicMock()
    skill_center.create_team.return_value = SkillCenterTeamCreateResult(team_id="sc-1")
    staff_dept = MagicMock()
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="owner-1", nick_name="  Creator  "
    )

    record, created = _make_service(repository, skill_center, staff_dept).initialize_personal(
        user_id="owner-1"
    )

    assert (record, created) == (personal, True)
    assert record.sc_team_id == "sc-1"
    staff_dept.get_profile_by_work_no.assert_called_once_with(work_no="owner-1")


def test_creator_profile_lookup_failure_degrades_to_null_name() -> None:
    repository = MagicMock()
    repository.get_personal_space.return_value = None
    personal = _space(space_type=SpaceType.PERSONAL)

    @contextmanager
    def create_transaction(*, user_id: str, creator_user_name: str | None, env: str):
        assert creator_user_name is None
        yield personal

    repository.create_personal_transaction.side_effect = create_transaction
    skill_center = MagicMock()
    skill_center.create_team.return_value = SkillCenterTeamCreateResult(team_id="sc-1")
    staff_dept = MagicMock()
    staff_dept.get_profile_by_work_no.side_effect = StaffProfileLookupError("down")

    record, created = _make_service(repository, skill_center, staff_dept).initialize_personal(
        user_id="owner-1"
    )

    assert (record, created) == (personal, True)
    assert record.sc_team_id == "sc-1"


def test_existing_personal_space_does_not_query_creator_profile() -> None:
    existing = _space(space_type=SpaceType.PERSONAL, sc_team_id="sc-existing")
    repository = MagicMock()
    repository.get_personal_space.return_value = existing
    staff_dept = MagicMock()

    assert _make_service(repository, MagicMock(), staff_dept).initialize_personal(
        user_id="owner-1"
    ) == (existing, False)
    staff_dept.get_profile_by_work_no.assert_not_called()


def test_create_team_can_skip_sc_creation() -> None:
    repository = _TransactionRepository(_space())
    skill_center = MagicMock()
    service = _make_service(repository, skill_center)

    record = service.create_team(
        name="Demo Team", creator_id="owner-1", create_sc_team=False
    )

    assert record == repository.record
    assert record.sc_team_id is None
    assert repository.committed is True
    skill_center.create_team.assert_not_called()


def test_initialize_personal_can_skip_sc_creation() -> None:
    personal = _space(space_type=SpaceType.PERSONAL)
    state = {"committed": False, "rolled_back": False}

    @contextmanager
    def create_transaction(*, user_id: str, creator_user_name: str | None, env: str):
        try:
            yield personal
        except Exception:
            state["rolled_back"] = True
            raise
        else:
            state["committed"] = True

    repository = MagicMock()
    repository.get_personal_space.return_value = None
    repository.create_personal_transaction.side_effect = create_transaction
    skill_center = MagicMock()
    service = _make_service(repository, skill_center)

    record, created = service.initialize_personal(
        user_id="owner-1", create_sc_team=False
    )

    assert (record, created) == (personal, True)
    assert record.sc_team_id is None
    assert state == {"committed": True, "rolled_back": False}
    skill_center.create_team.assert_not_called()


def test_initialize_personal_skip_does_not_repair_existing_binding() -> None:
    existing = _space(space_type=SpaceType.PERSONAL)
    repository = MagicMock()
    repository.get_personal_space.return_value = existing
    skill_center = MagicMock()
    service = _make_service(repository, skill_center)

    record, created = service.initialize_personal(
        user_id="owner-1", create_sc_team=False
    )

    assert (record, created) == (existing, False)
    repository.personal_sc_team_binding_transaction.assert_not_called()
    skill_center.get_team_by_ref_source.assert_not_called()
    skill_center.create_team.assert_not_called()
