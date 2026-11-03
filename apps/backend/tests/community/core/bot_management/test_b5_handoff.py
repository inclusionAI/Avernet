"""B5 stable grant/file addresses and read-only executor handoff policy."""

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.adapters.http.resources.file_router import _authorize_preview_bot, _resolve_params
from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
from agentclaw.community.adapters.http.openapi_v1.authorized_apps.gating import resolve_delegable_bot
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError
from agentclaw.community.core.bot_app_grant.models import BotAppGrantLogModel
from agentclaw.community.core.bot_app_grant.services.grant_service import BotAppGrantService
from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.execution_identity.contracts import (
    ExecutionIdentityOperationNotAllowedError, ExecutionIdentityStatus, ExecutionIdentityType,
    ExecutionIdentityBinding,
)
from agentclaw.community.core.execution_identity.service import ExecutionIdentityService
from agentclaw.community.core.execution_identity.models import BotExecutionIdentityBindingModel
from agentclaw.community.core.repository.implementations.identity.execution_identity import ExecutionIdentityRepository
from agentclaw.community.core.repository.implementations.bot.app_grant import BotAppGrantRepository
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.plugin_api.models import BotModel
from tests.community.core.bot_management.test_resource_handoff import Database, handoff


@pytest.fixture
def persisted(monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "dev")
    db = Database()
    bots = BotRepository(db)
    bot = bots.insert({
        "bot_id": "service", "entity_id": "A", "owner_id": "A", "creator_id": "A",
        "entity_type": "staff", "bot_type": "service", "active_engine": "moltis", "status": "ACTIVE",
    })
    yield db, bots, bot
    db.engine.dispose()


def test_grant_survives_handoff_without_changing_delegator_or_history(persisted):
    db, bots, bot = persisted
    service = BotAppGrantService(BotAppGrantRepository(db), bots)
    record = service.grant(bot_id="service", user_id="delegate", owner_id="A", app_id=7, app_name="App")
    handoff(db, bot["id"])
    assert service.find(bot_id="service", owner_id="B", user_id="delegate", app_id=7).id == record.id
    assert service.find(bot_id="service", owner_id="A", user_id="delegate", app_id=7).id == record.id
    assert service.find(bot_id="service", owner_id="B", user_id="B", app_id=7) is None
    assert [row.id for row in service.list_for_app(app_id=7, user_id="delegate")] == [record.id]
    assert service.grant(bot_id="service", user_id="delegate", owner_id="B", app_id=7, app_name="App").id == record.id
    new = service.grant(bot_id="service", user_id="B", owner_id="B", app_id=7, app_name="App")
    assert new.owner_id == "A" and new.user_id == "B"
    service.revoke_app(bot_id="service", owner_id="B", app_id=7)
    assert service.list_for_bot(bot_id="service", owner_id="A") == []
    with db.orm_session() as session:
        events = session.query(BotAppGrantLogModel).all()
        assert len(events) == 4
        assert {event.owner_id for event in events} == {"A"}
        assert {event.user_id for event in events} == {"delegate", "B"}


def test_file_resource_uses_entity_but_preview_authorizes_current_owner(persisted):
    db, bots, bot = persisted
    handoff(db, bot["id"])
    actor = MagicMock(user_id="B", bot_id="service")
    assert _resolve_params(actor, "service", None, "B", bot_repo=bots) == ("A", "service", "moltis")
    collaborators = MagicMock()
    assert _authorize_preview_bot(bot_id="service", requested_owner_id="A", user_id="B",
                                  bot_repo=bots, collaborator_svc=collaborators) == "A"
    collaborators.check_collaborator_permission.assert_not_called()
    collaborators.check_collaborator_permission.return_value = {"has_permission": False}
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        _authorize_preview_bot(bot_id="service", requested_owner_id="A", user_id="A",
                               bot_repo=bots, collaborator_svc=collaborators)
    assert error.value.status_code == 403


def test_grant_entity_liveness_does_not_follow_a_different_bots_owner(persisted):
    db, bots, bot = persisted
    handoff(db, bot["id"])
    with db.orm_session() as session:
        session.get(BotModel, bot["id"]).is_delete = 1
    bots.insert({"bot_id": "service", "entity_id": "C", "owner_id": "A", "creator_id": "A",
                 "entity_type": "staff", "bot_type": "service"})
    assert bots.get_live_bot_owners_by_entity([("service", "A")]) == {}


def test_deleted_bot_final_sweep_still_resolves_its_stable_grants(persisted):
    db, bots, bot = persisted
    service = BotAppGrantService(BotAppGrantRepository(db), bots)
    service.grant(bot_id="service", user_id="delegate", owner_id="A", app_id=7, app_name="App")
    handoff(db, bot["id"])
    with db.orm_session() as session:
        session.get(BotModel, bot["id"]).is_delete = 1
    assert service.revoke_all_for_bot(bot_id="service", owner_id="B") == 1
    assert service.revoke_all_for_bot(bot_id="service", owner_id="B") == 0


def test_app_owned_listing_projects_live_owner_without_rewriting_grant_locator(persisted):
    db, bots, bot = persisted
    grants = BotAppGrantService(BotAppGrantRepository(db), bots)
    grants.grant(bot_id="service", user_id="A", owner_id="A", app_id=7, app_name="App")
    handoff(db, bot["id"])
    grants.grant(bot_id="service", user_id="B", owner_id="B", app_id=7, app_name="App")
    record = grants.list_for_app(app_id=7, user_id="B")[0]
    assert record.owner_id == "A" and record.current_owner_id == "B"
    from agentclaw.community.adapters.http.openapi_v1.authorized_apps.router import _to_authorized_app, _to_authorized_bot
    assert _to_authorized_app(record, current_owner_id="B").owner_id == "B"
    assert _to_authorized_bot(record).owner_id == "B"
    assert ActingCaller(user_id="B", app_id=7, grants=grants).granted_bot_ids(owned_by_delegator=True) == {"service"}
    assert ActingCaller(user_id="A", app_id=7, grants=grants).granted_bot_ids(owned_by_delegator=True) == set()


@pytest.mark.parametrize("locator", ["A", "B"])
def test_authorized_app_gate_resolves_locator_and_checks_current_owner(persisted, locator):
    db, bots, bot = persisted
    handoff(db, bot["id"])

    class Permissions:
        def get_operable_permission_level(self, *, bot, user_id, env=None):
            return PermissionLevel.OWNER if bot["owner_id"] == user_id else PermissionLevel.NONE

    context = resolve_delegable_bot(Permissions(), bot_id="service", caller_id="B",
                                     owner_id=locator, bot_repository=bots)
    assert (context.current_owner_id, context.bot_pk, context.entity_id) == ("B", bot["id"], "A")
    with pytest.raises(BotNotFoundError):
        resolve_delegable_bot(Permissions(), bot_id="service", caller_id="A",
                              owner_id=locator, bot_repository=bots)


def test_collaboration_metadata_pages_by_pk_and_stable_entity_not_owner_snapshot(persisted):
    db, bots, bot = persisted
    handoff(db, bot["id"])
    total, rows = bots.page_live_bots_by_pks([bot["id"]], exclude_owner_id="delegate", entity_pairs=[("service", "A")], page=1, page_size=20)
    assert total == 1 and rows[0]["owner_id"] == "B"
    assert bots.page_live_bots_by_pks([bot["id"]], exclude_owner_id="B", entity_pairs=None, page=1, page_size=20) == (0, [])
    assert bots.page_live_bots_by_pks([bot["id"]], exclude_owner_id="delegate", entity_pairs=[("service", "B")], page=1, page_size=20) == (0, [])


def test_explicit_maintenance_env_reads_its_own_independent_execution_binding(persisted):
    db, bots, _ = persisted
    with db.orm_session() as session:
        target = BotModel(bot_id="service", entity_id="A", owner_id="B", creator_id="A",
                          entity_type="staff", bot_type="service", env="pre")
        session.add(target)
        session.flush()
        target_pk = target.id
        session.add(BotExecutionIdentityBindingModel(
            bot_pk=target_pk, env="pre", execution_workno="target-executor",
            identity_type="STAFF", status="ACTIVE", modifier_id="operator",
        ))
    assert bots.get_by_id_and_owner("service", "B", env="pre")["id"] == target_pk
    repository = ExecutionIdentityRepository(db)
    assert repository.get_active(bot_pk=target_pk) is None
    assert repository.get_active(bot_pk=target_pk, env="pre").execution_workno == "target-executor"


def test_ambiguous_grant_locator_cannot_read_or_revoke(persisted):
    db, bots, bot = persisted
    handoff(db, bot["id"])
    bots.insert({"bot_id": "service", "entity_id": "C", "owner_id": "A", "creator_id": "A",
                 "entity_type": "staff", "bot_type": "service"})
    repository = BotAppGrantRepository(db)
    with pytest.raises(BotLookupAmbiguousError):
        repository.find("service", "A", "delegate", 7)
    with pytest.raises(BotLookupAmbiguousError):
        repository.revoke_all_for_bot("service", "A")


@pytest.mark.parametrize("kind,bot_id", [("personal", "personal"), ("service", "default")])
def test_handoff_plan_refuses_personal_and_default(persisted, kind, bot_id):
    db, bots, bot = persisted
    with db.orm_session() as session:
        row = session.get(BotModel, bot["id"])
        row.bot_type, row.bot_id = kind, bot_id
    service = ExecutionIdentityService(bots, MagicMock(), MagicMock(), MagicMock())
    with pytest.raises(ExecutionIdentityOperationNotAllowedError):
        service.plan_owner_handoff(bot_pk=bot["id"], new_owner_id="B")


@pytest.mark.parametrize("independent", [False, True])
def test_handoff_plan_follows_new_owner_only_without_independent_binding(persisted, independent):
    _, bots, bot = persisted
    bindings, passport, runtime = MagicMock(), MagicMock(), MagicMock()
    bindings.get_pending.return_value = None
    active = ExecutionIdentityBinding(id=1, bot_pk=bot["id"], execution_workno="executor",
        identity_type=ExecutionIdentityType.STAFF, status=ExecutionIdentityStatus.ACTIVE)
    bindings.get_active.return_value = active if independent else None
    service = ExecutionIdentityService(bots, bindings, passport, runtime)
    plan = service.plan_owner_handoff(bot_pk=bot["id"], new_owner_id="B")
    assert plan.entity_id == "A" and plan.current_owner_id == "A"
    assert plan.execution_workno == ("executor" if independent else "B")
    assert plan.requires_reissue is (not independent)
    passport.reissue_agent_credentials.assert_not_called()
    runtime.hot_update_passport_token_to_device.assert_not_called()
    assert bots.get_by_pk(bot["id"])["owner_id"] == "A"
    bindings.get_pending.return_value = replace(active, status=ExecutionIdentityStatus.PENDING)
    with pytest.raises(ExecutionIdentityOperationNotAllowedError):
        service.plan_owner_handoff(bot_pk=bot["id"], new_owner_id="B")
