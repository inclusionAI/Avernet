"""Employee lookup format must not become the persisted resource/user identity."""

from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.owner_handoff.contracts import HandoffError
from agentclaw.community.core.bot_management.owner_handoff.models import OwnerHandoffModel
from agentclaw.community.core.bot_management.owner_handoff.owner_identity import (
    directory_work_no,
    owner_user_id,
)
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.plugin_api.staff_dept import UserIdentityInfo
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from tests.community.core.bot_management.test_owner_handoff import drain, world  # noqa: F401
from tests.community.core.bot_management.test_resource_handoff import persisted  # noqa: F401


@pytest.mark.parametrize("value,canonical,lookup", [
    ("012345", "12345", "012345"),
    ("12345", "12345", "012345"),
    ("001234", "1234", "001234"),
    ("123456", "123456", "123456"),
    ("1234567", "1234567", "1234567"),
    ("0123456", "0123456", "0123456"),
    ("A012345", "A012345", "A012345"),
    ("０１２３４５", "０１２３４５", "０１２３４５"),
])
def test_internal_employee_aliases_are_scoped(value, canonical, lookup):
    with avernet_tenant_scope("teamclaw"):
        assert owner_user_id(value) == canonical
        assert directory_work_no(canonical) == lookup
    with avernet_tenant_scope("external-tenant"):
        assert owner_user_id(value) == value
        assert directory_work_no(value) == value


def create(world, service, target, key="identity-request"):
    return service.create(
        bot_pk=world.bot["id"], actor_id="A", new_owner_id=target, request_key=key,
    )


def assign_space(world, space_id):
    with world.db.orm_session() as db:
        db.get(BotModel, world.bot["id"]).space_id = space_id


@pytest.mark.parametrize("target", ["012345", "12345"])
@pytest.mark.parametrize("space_kind", ["team", "personal", "legacy"])
def test_handoff_uses_directory_number_but_persists_login_identity(world, target, space_kind):
    if space_kind == "team":
        with world.spaces_repo.create_team_transaction(
            name="Team", creator_id="A", creator_user_name=None, env="dev",
        ) as team:
            space_id = team.id
        world.spaces_repo.add_member(
            space_id=space_id, user_id="12345", role=SpaceRole.MEMBER,
            creator_id="A", env="dev",
        )
        assign_space(world, space_id)
    elif space_kind == "personal":
        source = world.spaces.initialize_personal(user_id="A", create_sc_team=False)[0]
        assign_space(world, source.id)
    service = world.service()
    lookup = Mock(return_value=UserIdentityInfo(work_no="012345", display_name="New Owner"))
    service._staff.get_user_by_work_no = lookup
    world.passport.target_owner = "12345"
    row = create(world, service, target)
    lookup.assert_called_once_with(work_no="012345")
    assert row["new_owner_id"] == "12345"
    assert row["execution_workno"] == "12345"
    drain(world, row["operation_id"])
    bot = world.bots.get_by_pk(world.bot["id"])
    assert bot["owner_id"] == "12345" and bot["entity_id"] == "A"
    if space_kind == "team":
        assert bot["space_id"] == space_id
    elif space_kind == "personal":
        personal = world.spaces_repo.get_personal_space(user_id="12345", env="dev")
        assert personal is not None and bot["space_id"] == personal.id
        assert world.spaces_repo.get_personal_space(user_id="012345", env="dev") is None
    else:
        assert bot["space_id"] is None
    assert all(issue["owner_workno"] == "12345" for issue in world.passport.issues)
    assert len(world.passport.issues) == 1
    # Same request in the other spelling is replayed without another HR call,
    # space migration or credential issuance, even when HR is now unavailable.
    lookup.side_effect = RuntimeError("directory unavailable")
    replay = create(world, service, "12345" if target == "012345" else "012345")
    assert replay["operation_id"] == row["operation_id"]
    lookup.assert_called_once()
    with pytest.raises(HandoffError, match="IDEMPOTENCY_CONFLICT"):
        create(world, service, "12346")


def test_pending_alias_replay_does_not_need_directory_or_reissue(world):
    service = world.service()
    row = create(world, service, "12345")
    service._staff.get_user_by_work_no = Mock(side_effect=AssertionError("no HR on replay"))
    assert create(world, service, "012345")["operation_id"] == row["operation_id"]
    assert not world.passport.issues


def test_existing_padded_journal_is_replayed_without_rewriting_it(world):
    service = world.service()
    row = create(world, service, "12345")
    with world.db.orm_session() as db:
        db.get(OwnerHandoffModel, row["operation_id"]).new_owner_id = "012345"
    replay = create(world, service, "12345")
    assert replay["operation_id"] == row["operation_id"]
    assert replay["new_owner_id"] == "012345"
    # The transaction-level replay fence agrees with the service's fast path.
    replay = world.repo.begin(
        bot_pk=world.bot["id"], actor_id="A", new_owner_id="12345",
        new_owner_name="New Owner", request_key="identity-request",
        administrators=frozenset(),
    )
    assert replay["new_owner_id"] == "012345"


@pytest.mark.parametrize("profile", [
    UserIdentityInfo(work_no="012346", display_name="Other User"),
    UserIdentityInfo(work_no="012345"),
])
def test_directory_mismatch_or_missing_identity_still_rejected(world, profile):
    service = world.service()
    service._staff.get_user_by_work_no = Mock(return_value=profile)
    with pytest.raises(HandoffError, match="NEW_OWNER_NOT_FOUND"):
        create(world, service, "12345")
    assert not world.repo.recoverable() and not world.passport.issues


@pytest.mark.parametrize("target", [" 012345", "12345 ", ""])
def test_invalid_input_is_not_silently_trimmed(world, target):
    with pytest.raises(HandoffError, match="INVALID_NEW_OWNER"):
        create(world, world.service(), target)


def test_another_employees_membership_does_not_authorize_target(world):
    with world.spaces_repo.create_team_transaction(
        name="Team", creator_id="A", creator_user_name=None, env="dev",
    ) as team:
        space_id = team.id
    world.spaces_repo.add_member(
        space_id=space_id, user_id="12346", role=SpaceRole.MEMBER,
        creator_id="A", env="dev",
    )
    assign_space(world, space_id)
    with pytest.raises(HandoffError, match="NEW_OWNER_SPACE_MEMBERSHIP_REQUIRED"):
        create(world, world.service(), "012345")
    assert not world.repo.recoverable() and not world.passport.issues


def test_alias_of_current_owner_is_not_a_new_target(world):
    with world.db.orm_session() as db:
        db.get(BotModel, world.bot["id"]).owner_id = "12345"
    service = world.service()
    service._staff.get_user_by_work_no = Mock(side_effect=AssertionError("no HR on self handoff"))
    with pytest.raises(HandoffError, match="INVALID_OWNER_HANDOFF"):
        service.create(
            bot_pk=world.bot["id"], actor_id="12345", new_owner_id="012345",
            request_key="self-handoff",
        )
    assert not world.repo.recoverable() and not world.passport.issues
