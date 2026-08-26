"""SQLite-backed tests for Space repository persistence behavior."""

import asyncio
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.implementations.spaces.space import (
    SpaceRepository,
)
from agentclaw.community.core.repository.implementations.work_orders.work_order import (
    WorkOrderRepository,
)
from agentclaw.community.core.spaces.errors import (
    SpaceAlreadyExistsError,
    SpaceMemberAlreadyExistsError,
    SpaceNotFoundError,
)
from agentclaw.community.core.spaces.models import SpaceJoinStatus, SpaceRole, SpaceType
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.plugins.local.database import SqliteDB, reset_for_tests


@pytest.fixture
def db():
    reset_for_tests()
    plugin = SqliteDB()
    asyncio.run(plugin.bootstrap())
    yield plugin
    reset_for_tests()


def _team(spaces: SpaceRepository, name="Team", creator="owner-1", suffix=""):
    with spaces.create_team_transaction(
        name=name, creator_id=creator, creator_user_name=None, env="dev"
    ) as row:
        row.sc_team_id = f"sc-{name}-{creator}{suffix}"
        return row


def test_create_personal_transaction_preserves_unrelated_integrity_error() -> None:
    db = MagicMock()
    session = db.transactional_orm_session.return_value.__enter__.return_value
    original = IntegrityError("insert", {}, RuntimeError("write failed"))
    session.flush.side_effect = original
    repository = SpaceRepository(db)
    repository.get_personal_space = MagicMock(return_value=None)

    with pytest.raises(IntegrityError) as raised:
        with repository.create_personal_transaction(
            user_id="user-1", creator_user_name=None, env="dev"
        ):
            pass

    assert raised.value is original


def test_create_personal_transaction_translates_unique_race() -> None:
    db = MagicMock()
    session = db.transactional_orm_session.return_value.__enter__.return_value
    session.flush.side_effect = IntegrityError(
        "insert", {}, RuntimeError("duplicate personal owner")
    )
    repository = SpaceRepository(db)
    repository.get_personal_space = MagicMock(return_value=object())

    with pytest.raises(SpaceAlreadyExistsError, match="personal space already exists"):
        with repository.create_personal_transaction(
            user_id="user-1", creator_user_name=None, env="dev"
        ):
            pass


def test_initialize_personal_recovers_after_concurrent_unique_race() -> None:
    repository = SpaceRepository(MagicMock())
    winner = object()
    repository.get_personal_space = MagicMock(side_effect=[None, winner])

    @contextmanager
    def conflicting_transaction(*, user_id: str, creator_user_name: str | None, env: str):
        raise SpaceAlreadyExistsError("personal space already exists")
        yield  # pragma: no cover - contextmanager requires a generator

    repository.create_personal_transaction = conflicting_transaction

    assert repository.initialize_personal(user_id="user-1", env="dev") == (
        winner,
        False,
    )


def test_initialize_personal_reraises_race_when_winner_is_not_visible() -> None:
    repository = SpaceRepository(MagicMock())
    repository.get_personal_space = MagicMock(side_effect=[None, None])

    @contextmanager
    def conflicting_transaction(*, user_id: str, creator_user_name: str | None, env: str):
        raise SpaceAlreadyExistsError("personal space already exists")
        yield  # pragma: no cover - contextmanager requires a generator

    repository.create_personal_transaction = conflicting_transaction

    with pytest.raises(SpaceAlreadyExistsError, match="personal space already exists"):
        repository.initialize_personal(user_id="user-1", env="dev")


def test_personal_sc_binding_rejects_missing_space(db) -> None:
    repository = SpaceRepository(db)

    with pytest.raises(SpaceNotFoundError, match="personal space 999 not found"):
        with repository.personal_sc_team_binding_transaction(space_id=999, env="dev"):
            pass


def test_space_creation_persists_creator_user_name_and_lists_it(db) -> None:
    repository = SpaceRepository(db)
    with repository.create_personal_transaction(
        user_id="personal-1", creator_user_name="Personal Creator", env="dev"
    ) as personal:
        personal.sc_team_id = "sc-personal"
    with repository.create_team_transaction(
        name="Team", creator_id="team-owner", creator_user_name="Team Creator", env="dev"
    ) as team:
        team.sc_team_id = "sc-team"

    assert repository.get_member(
        space_id=personal.id, user_id="personal-1", env="dev"
    ).user_name == "Personal Creator"
    assert repository.get_member(
        space_id=team.id, user_id="team-owner", env="dev"
    ).user_name == "Team Creator"

    _, summaries = repository.list_spaces(
        user_id="team-owner", env="dev", keyword=None, space_type=None, offset=0, limit=20
    )
    assert next(item for item in summaries if item.space.id == team.id).creator_user_name == "Team Creator"


def test_get_team_space_by_name_filters_creator_environment_type_and_deleted_rows(db) -> None:
    repository = SpaceRepository(db)
    matching = _team(repository, name="Same", creator="owner-1")
    _team(repository, name="Same", creator="owner-2")
    _team(repository, name="Same", creator="owner-1", suffix="-duplicate")

    with repository.create_personal_transaction(
        user_id="owner-1", creator_user_name=None, env="dev"
    ) as personal:
        personal.sc_team_id = "sc-personal"

    with repository.create_team_transaction(
        name="Same", creator_id="owner-1", creator_user_name=None, env="pre"
    ) as other_env:
        other_env.sc_team_id = "sc-pre"

    with db.transactional_orm_session() as session:
        session.query(SpaceModel).filter(SpaceModel.id == matching.id).update(
            {SpaceModel.deleted_at: datetime(2026, 8, 25, 12, 0, 0)}
        )

    result = repository.get_team_space_by_name(
        creator_id="owner-1", name="Same", env="dev"
    )
    assert result is not None
    assert result.created_by == "owner-1"
    assert result.name == "Same"
    assert result.space_type is SpaceType.TEAM
    assert repository.get_team_space_by_name(
        creator_id="owner-1", name="Same", env="pre"
    ).id == other_env.id
    assert repository.get_team_space_by_name(
        creator_id="missing", name="Same", env="dev"
    ) is None
    assert repository.get_team_space_by_name(
        creator_id="owner-1", name="个人空间", env="dev"
    ) is None


def test_space_repository_full_member_lifecycle(db) -> None:
    repository = SpaceRepository(db)

    personal, created = repository.initialize_personal(user_id="user-1", env="dev")
    same, created_again = repository.initialize_personal(user_id="user-1", env="dev")
    team = _team(repository)

    assert created is True
    assert created_again is False
    assert same.id == personal.id
    personal_admin = repository.get_member(
        space_id=personal.id, user_id="user-1", env="dev"
    )
    assert personal_admin is not None
    assert personal_admin.role is SpaceRole.ADMIN
    with repository.personal_sc_team_binding_transaction(
        space_id=personal.id, env="dev"
    ) as personal_binding:
        personal_binding.sc_team_id = "sc-personal-user-1"
    assert (
        repository.get_personal_space(user_id="user-1", env="dev").sc_team_id
        == "sc-personal-user-1"
    )
    assert (
        repository.get_space(space_id=team.id, env="dev").sc_team_id
        == "sc-Team-owner-1"
    )
    assert repository.get_space(space_id=999, env="dev") is None
    assert repository.get_space_by_code(space_code=team.space_code, env="dev").model_dump(
        exclude={"gmt_created", "gmt_modified"}
    ) == team.model_dump(exclude={"gmt_created", "gmt_modified"})
    assert repository.get_space_by_code(space_code="missing", env="dev") is None

    other_env_personal, _ = repository.initialize_personal(
        user_id="user-other-env", env="pre"
    )
    batch = repository.batch_query_personal(
        user_ids=["missing", "user-1", "user-other-env"], env="dev"
    )
    assert [item.model_dump() for item in batch] == [
        {"user_id": "missing", "space_id": None, "found": False},
        {"user_id": "user-1", "space_id": personal.id, "found": True},
        {"user_id": "user-other-env", "space_id": None, "found": False},
    ]
    assert other_env_personal.id is not None

    total, spaces = repository.list_spaces(
        user_id="user-1", env="dev", keyword=None, space_type=None, offset=0, limit=20
    )
    assert total == 2
    assert {item.space.id for item in spaces} == {personal.id, team.id}
    personal_summary = next(item for item in spaces if item.space.id == personal.id)
    assert personal_summary.current_user_role is SpaceRole.ADMIN

    filtered_total, filtered = repository.list_spaces(
        user_id="owner-1",
        env="dev",
        keyword="Tea",
        space_type=SpaceType.TEAM.value,
        offset=0,
        limit=20,
    )
    assert filtered_total == 1
    assert filtered[0].space.id == team.id

    added = repository.add_member(
        space_id=team.id,
        user_id="member-1",
        user_name="Member One",
        role=SpaceRole.MEMBER,
        creator_id="owner-1",
        env="dev",
    )
    assert (
        repository.get_member(space_id=team.id, user_id="member-1", env="dev") == added
    )
    assert added.user_name == "Member One"
    assert repository.get_member(space_id=team.id, user_id="missing", env="dev") is None
    with pytest.raises(SpaceMemberAlreadyExistsError):
        repository.add_member(
            space_id=team.id,
            user_id="member-1",
            role=SpaceRole.MEMBER,
            creator_id="owner-1",
            env="dev",
        )

    member_total, members = repository.list_members(
        space_id=team.id, env="dev", keyword="member", offset=0, limit=20
    )
    assert member_total == 1
    assert members[0].is_creator is False
    assert members[0].member.user_name == "Member One"
    updated = repository.update_member_role(
        space_id=team.id, user_id="member-1", role=SpaceRole.OWNER, env="dev"
    )
    assert updated.role is SpaceRole.ADMIN
    assert (
        repository.update_member_role(
            space_id=team.id, user_id="missing", role=SpaceRole.OWNER, env="dev"
        )
        is None
    )
    assert (
        repository.delete_member(space_id=team.id, user_id="member-1", env="dev")
        is True
    )
    with db.orm_session() as session:
        assert (
            session.query(SpaceMemberModel)
            .filter(
                SpaceMemberModel.space_id == team.id,
                SpaceMemberModel.user_id == "member-1",
                SpaceMemberModel.env == "dev",
            )
            .count()
            == 0
        )
    assert (
        repository.delete_member(space_id=team.id, user_id="member-1", env="dev")
        is False
    )
    readded = repository.add_member(
        space_id=team.id,
        user_id="member-1",
        user_name=None,
        role=SpaceRole.MEMBER,
        creator_id="owner-1",
        env="dev",
    )
    assert readded.user_name is None


def test_space_repository_marks_pending_join_request_as_applying(db) -> None:
    spaces = SpaceRepository(db)
    work_orders = WorkOrderRepository(db)
    team = _team(spaces)
    other_team = _team(spaces, name="Other Team", creator="owner-2")

    work_orders.create_space_join_request(
        space_id=team.id,
        applicant_user_id="applicant-1",
        applicant_name="Applicant",
        apply_reason="join",
        env="dev",
    )

    total, items = spaces.list_spaces(
        user_id="applicant-1",
        env="dev",
        keyword=None,
        space_type=SpaceType.TEAM.value,
        offset=0,
        limit=20,
    )

    assert total == 2
    statuses = {item.space.id: item.join_status for item in items}
    assert statuses[team.id] is SpaceJoinStatus.APPLYING
    assert statuses[other_team.id] is SpaceJoinStatus.NOT_JOINED

    spaces.add_member(
        space_id=team.id,
        user_id="applicant-1",
        role=SpaceRole.MEMBER,
        creator_id="owner-1",
        env="dev",
    )
    _, joined_items = spaces.list_spaces(
        user_id="applicant-1",
        env="dev",
        keyword=None,
        space_type=SpaceType.TEAM.value,
        offset=0,
        limit=20,
    )
    joined = next(item for item in joined_items if item.space.id == team.id)
    assert joined.join_status is SpaceJoinStatus.JOINED


def test_space_repository_backfills_only_live_unbound_team_in_same_env(db) -> None:
    repository = SpaceRepository(db)
    target = SpaceModel(
        space_code="spc-repair-target",
        space_type=SpaceType.TEAM.value,
        name="Repair Target",
        personal_owner_id=None,
        sc_team_id=None,
        env="dev",
        created_by="owner",
        updated_by="owner",
    )
    personal = SpaceModel(
        space_code="spc-repair-personal",
        space_type=SpaceType.PERSONAL.value,
        name="Personal",
        personal_owner_id="personal-owner",
        sc_team_id=None,
        env="dev",
        created_by="personal-owner",
        updated_by="personal-owner",
    )
    already_bound = SpaceModel(
        space_code="spc-repair-bound",
        space_type=SpaceType.TEAM.value,
        name="Already Bound",
        personal_owner_id=None,
        sc_team_id="existing-team",
        env="dev",
        created_by="owner",
        updated_by="owner",
    )
    deleted = SpaceModel(
        space_code="spc-repair-deleted",
        space_type=SpaceType.TEAM.value,
        name="Deleted",
        personal_owner_id=None,
        sc_team_id=None,
        env="dev",
        created_by="owner",
        updated_by="owner",
        deleted_at=datetime(2026, 8, 20, 10, 0),
    )
    with db.orm_session() as session:
        session.add_all([target, personal, already_bound, deleted])
        session.flush()
        session.refresh(target)
        session.refresh(personal)
        session.refresh(already_bound)
        session.refresh(deleted)
        ids = {
            "target": target.id,
            "personal": personal.id,
            "bound": already_bound.id,
            "deleted": deleted.id,
        }

    assert (
        repository.backfill_sc_team_id(
            space_id=ids["target"], env="pre", sc_team_id="wrong-env"
        )
        is False
    )
    assert (
        repository.backfill_sc_team_id(
            space_id=ids["personal"], env="dev", sc_team_id="personal-team"
        )
        is False
    )
    assert (
        repository.backfill_sc_team_id(
            space_id=ids["bound"], env="dev", sc_team_id="replacement-team"
        )
        is False
    )
    assert (
        repository.backfill_sc_team_id(
            space_id=ids["deleted"], env="dev", sc_team_id="deleted-team"
        )
        is False
    )
    assert (
        repository.backfill_sc_team_id(
            space_id=ids["target"], env="dev", sc_team_id="repaired-team"
        )
        is True
    )
    assert (
        repository.backfill_sc_team_id(
            space_id=ids["target"], env="dev", sc_team_id="second-team"
        )
        is False
    )

    assert repository.get_space(space_id=ids["target"], env="dev").sc_team_id == (
        "repaired-team"
    )
    assert repository.get_space(space_id=ids["bound"], env="dev").sc_team_id == (
        "existing-team"
    )
    assert repository.get_space(space_id=ids["personal"], env="dev").sc_team_id is None
    assert repository.get_space(space_id=ids["deleted"], env="dev") is None
