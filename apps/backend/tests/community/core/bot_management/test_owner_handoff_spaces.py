"""Real handoff/Space persistence and the new workbench's visibility contract."""

from datetime import datetime

import pytest
from sqlalchemy import event

from agentclaw.community.adapters.bot_space_context import SpaceServiceBotSpaceContext
from agentclaw.community.core.bot_inventory.errors import BotInventoryPermissionError
from agentclaw.community.core.bot_management.owner_handoff.contracts import HandoffError
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.spaces.repository.models import SpaceMemberModel, SpaceModel
from agentclaw.community.plugin_api.models import BotModel
from tests.community.core.bot_management.test_owner_handoff import (
    begin,
    drain,
    world,  # noqa: F401
)
from tests.community.core.bot_management.test_resource_handoff import persisted  # noqa: F401


def _assign(world, space_id):
    with world.db.orm_session() as db:
        db.get(BotModel, world.bot["id"]).space_id = space_id


def _personal(world, user):
    return world.spaces.initialize_personal(user_id=user, create_sc_team=False)[0]


def _team(world, *, member):
    with world.spaces_repo.create_team_transaction(
        name="Team", creator_id="A", creator_user_name=None, env="dev"
    ) as team:
        space_id = team.id
    if member:
        world.spaces_repo.add_member(
            space_id=space_id, user_id="B", role=SpaceRole.MEMBER,
            creator_id="A", env="dev",
        )
    _assign(world, space_id)
    return space_id


@pytest.mark.parametrize("target_exists", [False, True])
def test_personal_handoff_moves_with_owner_and_is_visible_in_new_workbench(world, target_exists):
    source = _personal(world, "A")
    _assign(world, source.id)
    target = _personal(world, "B") if target_exists else None
    op = begin(world)
    target = world.spaces_repo.get_personal_space(user_id="B", env="dev")
    assert target is not None
    assert world.bots.get_by_pk(world.bot["id"])["space_id"] == source.id
    drain(world, op)
    bot = world.bots.get_by_pk(world.bot["id"])
    assert (bot["owner_id"], bot["space_id"], bot["entity_id"]) == ("B", target.id, "A")
    context = SpaceServiceBotSpaceContext(world.spaces, world.space_access)
    current = context.resolve_current(owner_id="B", header_space_id=None)
    context.assert_bot_visible_in_current_space(bot=bot, owner_id="B", current_space=current)
    with pytest.raises(BotInventoryPermissionError):
        context.assert_bot_visible_in_current_space(
            bot=bot, owner_id="A",
            current_space=context.resolve_current(owner_id="A", header_space_id=None),
        )
    # An idempotent replay must neither reissue nor move again.
    assert begin(world) == op
    assert len(world.passport.issues) == 1
    world.space_client.create_team.assert_not_called()


def test_team_handoff_keeps_team_and_memberships(world):
    team_id = _team(world, member=True)
    op = begin(world)
    drain(world, op)
    bot = world.bots.get_by_pk(world.bot["id"])
    assert (bot["owner_id"], bot["space_id"]) == ("B", team_id)
    for user in ("A", "B"):
        assert world.spaces_repo.get_member(space_id=team_id, user_id=user, env="dev")
    assert world.spaces_repo.get_personal_space(user_id="B", env="dev") is None


@pytest.mark.parametrize("status", [None, "INACTIVE"])
def test_team_requires_active_new_owner_membership_before_freezing(world, status):
    team_id = _team(world, member=status is not None)
    if status:
        with world.db.orm_session() as db:
            db.query(SpaceMemberModel).filter_by(space_id=team_id, user_id="B").one().status = status
    with pytest.raises(HandoffError, match="NEW_OWNER_SPACE_MEMBERSHIP_REQUIRED"):
        begin(world)
    bot = world.bots.get_by_pk(world.bot["id"])
    assert (bot["owner_id"], bot["space_id"]) == ("A", team_id)
    assert not (bot["ext"] or {}).get("owner_handoff")
    assert world.repo.recoverable() == []
    assert world.passport.issues == []


def test_membership_removed_during_cutover_blocks_finish_then_recovers(world):
    team_id = _team(world, member=True)
    op = begin(world)
    for _ in range(5):
        world.service().step(op)
    assert world.repo.get(op)["phase"] == "COMMITTING_OWNER"
    world.spaces_repo.delete_member(space_id=team_id, user_id="B", env="dev")
    assert not world.service().step(op)
    assert world.repo.get(op)["error_code"] == "NEW_OWNER_SPACE_MEMBERSHIP_REQUIRED"
    assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "A"
    world.spaces_repo.add_member(
        space_id=team_id, user_id="B", role=SpaceRole.MEMBER, creator_id="A", env="dev"
    )
    drain(world, op)
    assert len(world.passport.issues) == 1


def test_owner_space_and_unfreeze_roll_back_together(world, monkeypatch):
    source = _personal(world, "A")
    _assign(world, source.id)
    op = begin(world)
    for _ in range(5):
        world.service().step(op)
    original = world.repo._unfreeze

    def failing(db, row, bot):
        original(db, row, bot)

        def after_writes(session):
            session.flush()
            assert bot.owner_id == "B" and bot.space_id != source.id
            raise RuntimeError("commit failed after writes")

        event.listen(db, "before_commit", after_writes, once=True)

    monkeypatch.setattr(world.repo, "_unfreeze", failing)
    assert not world.service().step(op)
    bot = world.bots.get_by_pk(world.bot["id"])
    assert (bot["owner_id"], bot["space_id"]) == ("A", source.id)
    assert bot["ext"]["owner_handoff"] == op
    monkeypatch.setattr(world.repo, "_unfreeze", original)
    drain(world, op)
    assert world.bots.get_by_pk(world.bot["id"])["space_id"] != source.id


@pytest.mark.parametrize("invalid", ["deleted", "wrong-env", "other-person"])
def test_invalid_source_space_is_rejected_without_handoff(world, invalid):
    source = _personal(world, "C" if invalid == "other-person" else "A")
    _assign(world, source.id)
    with world.db.orm_session() as db:
        row = db.get(SpaceModel, source.id)
        if invalid == "deleted":
            row.deleted_at = datetime.now()
        elif invalid == "wrong-env":
            row.env = "prod"
    with pytest.raises(HandoffError, match="HANDOFF_.*SPACE"):
        begin(world)
    assert world.repo.recoverable() == []
    assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "A"


def test_legacy_null_space_stays_compatible(world):
    op = begin(world)
    drain(world, op)
    assert world.bots.get_by_pk(world.bot["id"])["space_id"] is None
    assert world.spaces_repo.get_personal_space(user_id="B", env="dev") is None


def test_unauthorized_request_does_not_initialize_target_space(world):
    _assign(world, _personal(world, "A").id)
    with pytest.raises(HandoffError, match="OWNER_REQUIRED"):
        world.service().create(
            bot_pk=world.bot["id"], actor_id="C", new_owner_id="B", request_key="denied"
        )
    assert world.spaces_repo.get_personal_space(user_id="B", env="dev") is None


def test_personal_initialization_failure_does_not_freeze_bot(world, monkeypatch):
    _assign(world, _personal(world, "A").id)

    def failed(**kwargs):
        raise RuntimeError("space write failed")

    monkeypatch.setattr(world.spaces, "initialize_personal", failed)
    with pytest.raises(RuntimeError, match="space write failed"):
        begin(world)
    assert world.repo.recoverable() == []
    assert not (world.bots.get_by_pk(world.bot["id"])["ext"] or {}).get("owner_handoff")


def test_deleted_target_blocks_commit_without_losing_source_assignment(world):
    source = _personal(world, "A")
    _assign(world, source.id)
    op = begin(world)
    target = world.spaces_repo.get_personal_space(user_id="B", env="dev")
    for _ in range(5):
        world.service().step(op)
    with world.db.orm_session() as db:
        db.get(SpaceModel, target.id).deleted_at = datetime.now()
    assert not world.service().step(op)
    assert world.repo.get(op)["error_code"] == "HANDOFF_TARGET_SPACE_REQUIRED"
    bot = world.bots.get_by_pk(world.bot["id"])
    assert (bot["owner_id"], bot["space_id"]) == ("A", source.id)
    with world.db.orm_session() as db:
        db.get(SpaceModel, target.id).deleted_at = None
    drain(world, op)
    assert world.bots.get_by_pk(world.bot["id"])["space_id"] == target.id
    assert len(world.passport.issues) == 1
