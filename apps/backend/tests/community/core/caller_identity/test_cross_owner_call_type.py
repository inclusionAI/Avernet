"""Admin call-type edits keep actor, Owner and immutable entity separate."""

import logging
from types import SimpleNamespace

import pytest

from agentclaw.community.core.bot_collaborator.models import BotCollabLockModel
from agentclaw.community.core.caller_identity.contracts import (
    CallerCliSyncError,
    CallerIdentityPermissionError,
    CallerLockEpochError,
    CallerMcpSyncError,
)
from agentclaw.community.core.caller_identity.models import BotCliCallConfigModel, BotMcpCallConfigModel, McpCallType
from agentclaw.community.plugin_api.models import BotModel
from tests.community.core.caller_identity.test_admin_owner_transition import (
    _persisted,
    _seed,
    _update,
    world as world,
)


@pytest.fixture
def cross_world(world, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.config.sofa.sofa_config",
        SimpleNamespace(user_config={"admin": {"super_admin": ["admin"]}}),
    )

    def current_lock(key):
        with world.database.orm_session() as session:
            lock = session.query(BotCollabLockModel).filter(
                BotCollabLockModel.lock_key == key, BotCollabLockModel.env == "dev",
            ).one_or_none()
            return (SimpleNamespace(id=lock.id, holder_user_id=lock.holder_user_id)
                    if lock is not None else None)

    world.lock_repository.get_by_key.side_effect = current_lock
    return world


def _lock(world, *, key="bot:entity", holder="admin"):
    with world.database.transactional_orm_session() as session:
        lock = BotCollabLockModel(lock_key=key, holder_user_id=holder, env="dev")
        session.add(lock)
        session.flush()
        return lock.id


def _assert_actor_and_address(world, resource, mode, owner="owner"):
    world.service._mcp_provider.collect_bot_active_mcps.assert_called_once_with(
        entity_id="entity", bot_id="bot", user_id=owner,
        entity_type="staff", engine_type="openclaw",
    )
    with world.database.orm_session() as session:
        bot = session.query(BotModel).filter(BotModel.id == 1).one()
        assert bot.owner_id == owner
        assert bot.entity_id == "entity"
        assert bot.modifier_id == "admin"
        if mode is McpCallType.CALLER:
            model = BotMcpCallConfigModel if resource == "mcp" else BotCliCallConfigModel
            assert session.query(model).filter(model.bot_pk == 1).one().modifier_id == "admin"
    if resource == "mcp":
        payload = world.mcp_sync.sync_mcp_identity_to_agent_principal.call_args.kwargs
        assert payload["user_id"] == owner
        assert payload["entity_id"] == "entity"
    else:
        world.passport.query_agent_passport.assert_called_with("bot", owner)
        payload = world.passport.update_passport.call_args.kwargs
        assert payload["user_id"] == owner
        cli = next(item for item in payload["resource_scope"]["cli_items"] if item["cli_code"] == "custom-cli")
        assert cli["identity_mode"] == mode.value


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
@pytest.mark.parametrize("mode", [McpCallType.CALLER, McpCallType.OWNER])
async def test_super_admin_edits_other_owner_without_impersonation(cross_world, resource, mode, caplog):
    _seed(cross_world, resource)
    with caplog.at_level(logging.INFO):
        result = await _update(cross_world, resource, actor_id="admin", call_type=mode)
    assert result.call_type is mode
    assert _persisted(cross_world) == (mode.value, 1)
    _assert_actor_and_address(cross_world, resource, mode)
    assert "caller_call_type_edit_authorized" in caplog.text
    assert "actor_id=admin" in caplog.text
    assert "owner_id=owner" in caplog.text
    assert "entity_id=entity" in caplog.text
    assert "reason=super_admin" in caplog.text
    assert "synthetic-secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
async def test_handoff_keeps_entity_lock_and_current_owner_address(cross_world, resource):
    _seed(cross_world, resource)
    cross_world.bot["owner_id"] = "new-owner"
    with cross_world.database.transactional_orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 1).one().owner_id = "new-owner"
    epoch = _lock(cross_world)
    result = await _update(cross_world, resource, actor_id="admin", lock_epoch=epoch)
    assert result.bot_call_type is McpCallType.OWNER
    cross_world.lock_repository.get_by_key.assert_called_with("bot:entity")
    _assert_actor_and_address(cross_world, resource, McpCallType.OWNER, owner="new-owner")


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
@pytest.mark.parametrize("actor", ["ordinary", "operator", "collaborator"])
async def test_non_admin_non_owner_is_denied(cross_world, resource, actor, caplog, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.config.sofa.sofa_config",
        SimpleNamespace(user_config={"admin": {
            "super_admin": ["admin"], "operator": ["operator"],
        }}),
    )
    _seed(cross_world, resource)
    with caplog.at_level(logging.INFO), pytest.raises(CallerIdentityPermissionError):
        await _update(cross_world, resource, actor_id=actor)
    assert _persisted(cross_world) == ("caller", 0)
    cross_world.service._mcp_provider.collect_bot_active_mcps.assert_not_called()
    assert "caller_call_type_edit_denied" in caplog.text
    assert f"actor_id={actor}" in caplog.text
    assert "reason=not_owner_or_super_admin" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
@pytest.mark.parametrize("holder,epoch_kind", [
    ("owner", "valid"), ("admin", "missing"), ("admin", "stale"),
])
async def test_admin_cannot_bypass_target_lock(cross_world, resource, holder, epoch_kind):
    _seed(cross_world, resource)
    epoch = _lock(cross_world, holder=holder)
    supplied = None if epoch_kind == "missing" else epoch + (1 if epoch_kind == "stale" else 0)
    with pytest.raises(CallerLockEpochError):
        await _update(cross_world, resource, actor_id="admin", lock_epoch=supplied)
    assert _persisted(cross_world) == ("caller", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
async def test_irrelevant_admin_lock_does_not_replace_target_lock(cross_world, resource):
    _seed(cross_world, resource)
    _lock(cross_world, key="bot:admin")
    result = await _update(cross_world, resource, actor_id="admin")
    assert result.bot_call_type is McpCallType.OWNER
    cross_world.lock_repository.get_by_key.assert_called_with("bot:entity")


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
async def test_admin_edit_sync_failure_compensates_under_target_entity_lock(cross_world, resource, caplog):
    _seed(cross_world, resource)
    epoch = _lock(cross_world)
    if resource == "mcp":
        cross_world.mcp_sync.sync_mcp_identity_to_agent_principal.return_value = {"success": False}
        error = CallerMcpSyncError
    else:
        cross_world.passport.update_passport.side_effect = RuntimeError("synthetic-secret")
        error = CallerCliSyncError
    with caplog.at_level(logging.INFO), pytest.raises(error):
        await _update(cross_world, resource, actor_id="admin", lock_epoch=epoch)
    assert _persisted(cross_world) == ("caller", 2)
    assert "compensated" in caplog.text
    assert "synthetic-secret" not in caplog.text
    with cross_world.database.orm_session() as session:
        assert session.query(BotModel).filter(BotModel.id == 1).one().modifier_id == "admin"


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
async def test_target_lock_race_is_rechecked_in_repository(cross_world, resource):
    _seed(cross_world, resource)
    _lock(cross_world, holder="owner")
    cross_world.lock_repository.get_by_key.side_effect = None
    cross_world.lock_repository.get_by_key.return_value = None
    with pytest.raises(CallerLockEpochError):
        await _update(cross_world, resource, actor_id="admin")
    assert _persisted(cross_world) == ("caller", 0)


def test_shared_owner_only_asset_guard_does_not_inherit_admin_exception(cross_world):
    from agentclaw.community.core.caller_identity.guard import require_owner_of_active_service_bot

    with pytest.raises(CallerIdentityPermissionError):
        require_owner_of_active_service_bot(cross_world.bot, "admin")


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
@pytest.mark.parametrize("field,value", [("status", "DELETED"), ("bot_type", "personal")])
async def test_admin_does_not_bypass_active_service_requirement(cross_world, resource, field, value):
    from agentclaw.community.core.caller_identity.contracts import CallerIdentityReadOnlyError

    cross_world.bot[field] = value
    with pytest.raises(CallerIdentityReadOnlyError):
        await _update(cross_world, resource, actor_id="admin")
    assert _persisted(cross_world) == ("caller", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
async def test_admin_does_not_resolve_ambiguous_bot_arbitrarily(cross_world, resource):
    from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
    from agentclaw.community.core.caller_identity.contracts import CallerIdentityAmbiguousError

    cross_world.service._bot_repository.get_unique_by_id.side_effect = BotLookupAmbiguousError("ambiguous")
    with pytest.raises(CallerIdentityAmbiguousError):
        await _update(cross_world, resource, actor_id="admin", entity_id=None)
    assert _persisted(cross_world) == ("caller", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
@pytest.mark.parametrize("actor", ["admin", "ordinary"])
async def test_unknown_or_spoofed_entity_does_not_fall_back(cross_world, resource, actor):
    from agentclaw.community.core.caller_identity.contracts import CallerIdentityNotFoundError

    repository = cross_world.service._bot_repository
    repository.get_by_id_and_entity.return_value = None
    expected = CallerIdentityPermissionError if resource == "mcp" else CallerIdentityNotFoundError
    with pytest.raises(expected):
        await _update(cross_world, resource, actor_id=actor, entity_id="unrelated-entity")
    repository.get_by_id_and_entity.assert_called_once_with("bot", "unrelated-entity")
    repository.get_unique_by_id.assert_not_called()
    assert _persisted(cross_world) == ("caller", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
async def test_admin_cannot_edit_a_resource_not_in_target_bot(cross_world, resource):
    from agentclaw.community.core.caller_identity.contracts import CallerCliNotFoundError, CallerMcpNotFoundError

    cross_world.service._mcp_provider.collect_bot_active_mcps.return_value = []
    cross_world.passport.query_agent_passport.return_value = {"clis": [], "mcps": []}
    error = CallerMcpNotFoundError if resource == "mcp" else CallerCliNotFoundError
    with pytest.raises(error):
        await _update(cross_world, resource, actor_id="admin")
    assert _persisted(cross_world) == ("caller", 0)


@pytest.mark.asyncio
async def test_admin_cannot_edit_an_unsupported_cli_profile(cross_world):
    from agentclaw.community.core.caller_identity.contracts import CallerIdentityReadOnlyError

    cross_world.bot["active_engine"] = "unsupported-engine"
    with pytest.raises(CallerIdentityReadOnlyError):
        await _update(cross_world, "cli", actor_id="admin")
    cross_world.passport.query_agent_passport.assert_not_called()
    assert _persisted(cross_world) == ("caller", 0)
