"""Configured admin exceptions exercise real aggregate writes and rollback."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.bot_collaborator.models import BotCollabLockModel
from agentclaw.community.core.caller_identity.contracts import (
    CallerCliSyncError,
    CallerIdentityIrreversibleError,
    CallerIdentityPermissionError,
    CallerLockEpochError,
    CallerMcpSyncError,
)
from agentclaw.community.core.caller_identity.models import (
    BotCliCallConfigModel,
    BotMcpCallConfigModel,
    McpCallType,
)
from agentclaw.community.core.caller_identity.service import CallerIdentityService
from agentclaw.community.core.mcp.services.cli_passport_scope import CliPassportScopeReconciler
from agentclaw.community.core.repository.implementations.identity.caller_identity import CallerIdentityRepository
from agentclaw.community.plugin_api.models import BotModel
from tests.community.repository.identity.test_caller_identity_repository import InMemorySqliteDB


@pytest.fixture
def world(monkeypatch):
    # Membership comes from trusted config, never an HTTP input.
    monkeypatch.setattr(
        "agentclaw.community.core.config.sofa.sofa_config",
        SimpleNamespace(user_config={"admin": {"super_admin": ["owner"]}}),
    )
    database = InMemorySqliteDB()
    with database.transactional_orm_session() as session:
        session.add(BotModel(
            id=1, bot_id="bot", owner_id="owner", creator_id="owner",
            entity_id="entity", entity_type="staff", bot_type="service",
            status="ACTIVE", active_engine="openclaw", call_type="caller",
            caller_config_revision=0, env="dev",
        ))
    bot = {
        "id": 1, "bot_id": "bot", "owner_id": "owner", "entity_id": "entity",
        "entity_type": "staff", "bot_type": "service", "status": "ACTIVE",
        "active_engine": "openclaw", "call_type": "caller", "env": "dev",
    }
    repository = CallerIdentityRepository(database)
    bot_repository = MagicMock()
    bot_repository.get_by_id_and_entity.return_value = bot
    lock_repository = MagicMock()
    lock_repository.get_by_key.return_value = None
    passport = MagicMock()
    passport.query_agent_passport.return_value = {
        "clis": [{"cli_code": "custom-cli", "identity_mode": "caller"}],
        "mcps": [], "credentials": {"token": "synthetic-secret"},
    }
    mcp_provider = MagicMock()
    mcp_provider.collect_bot_active_mcps.return_value = [{"server_code": "mcp"}]
    mcp_sync = AsyncMock()
    mcp_sync.sync_mcp_identity_to_agent_principal.return_value = {"success": True}
    service = CallerIdentityService(
        bot_repository=bot_repository, collaborator_repository=MagicMock(),
        lock_repository=lock_repository, mcp_provider=mcp_provider,
        repository=repository, mcp_sync_service=mcp_sync, passport_plugin=passport,
        cli_scope_reconciler=CliPassportScopeReconciler(
            passport_plugin=passport, identity_repository=repository,
        ),
        session_validator=MagicMock(),
        bcs_caller_sync=MagicMock(),
    )
    return SimpleNamespace(
        database=database, repository=repository, service=service, bot=bot,
        passport=passport, mcp_sync=mcp_sync, lock_repository=lock_repository,
    )


def _seed(world, resource):
    with world.database.transactional_orm_session() as session:
        if resource == "mcp":
            session.add(BotMcpCallConfigModel(
                bot_pk=1, server_code="mcp", engine_type="openclaw",
                call_type="caller", modifier_id="owner", env="dev",
            ))
        else:
            session.add(BotCliCallConfigModel(
                bot_pk=1, cli_code="custom-cli", engine_type="openclaw",
                call_type="caller", modifier_id="owner", revision=1, env="dev",
            ))


async def _update(world, resource, **kwargs):
    args = dict(
        bot_id="bot", entity_id="entity", actor_id="owner", call_type=McpCallType.OWNER,
    )
    args.update(kwargs)
    if resource == "mcp":
        return await world.service.update_mcp_call_type(server_code="mcp", **args)
    return await world.service.update_cli_call_type(cli_code="custom-cli", **args)


def _persisted(world):
    with world.database.orm_session() as session:
        bot = session.query(BotModel).filter(BotModel.id == 1).one()
        return bot.call_type, bot.caller_config_revision


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
async def test_configured_super_admin_can_remove_last_caller(world, resource, caplog):
    _seed(world, resource)
    with caplog.at_level(logging.INFO):
        result = await _update(world, resource)
    assert result.bot_call_type is McpCallType.OWNER
    assert _persisted(world) == ("owner", 1)
    assert world.repository.list_draft_call_types(1, "openclaw") == {}
    assert world.repository.list_draft_cli_call_types(1, "openclaw") == {}
    assert "caller_owner_transition_authorized" in caplog.text
    assert "actor_id=owner" in caplog.text
    assert "synthetic-secret" not in caplog.text
    if resource == "mcp":
        request = world.mcp_sync.sync_mcp_identity_to_agent_principal.call_args.kwargs
        assert request["user_id"] == "owner"
        assert request["identity_modes"] == {}
    else:
        scope = world.passport.update_passport.call_args.kwargs["resource_scope"]
        target = next(item for item in scope["cli_items"] if item["cli_code"] == "custom-cli")
        assert target["identity_mode"] == "owner"
        assert world.passport.update_passport.call_args.kwargs["user_id"] == "owner"


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
@pytest.mark.parametrize("admin_config", [{}, {"super_admin": []}, {"operator": ["owner"]}])
async def test_ordinary_or_operator_owner_cannot_remove_last_caller(world, resource, admin_config, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.config.sofa.sofa_config",
        SimpleNamespace(user_config={"admin": admin_config}),
    )
    _seed(world, resource)
    with pytest.raises(CallerIdentityIrreversibleError):
        await _update(world, resource)
    assert _persisted(world) == ("caller", 0)
    world.passport.update_passport.assert_not_called()
    world.mcp_sync.sync_mcp_identity_to_agent_principal.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
async def test_non_admin_non_owner_and_admin_lock_checks(world, resource, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.config.sofa.sofa_config",
        SimpleNamespace(user_config={"admin": {"super_admin": ["owner"]}}),
    )
    _seed(world, resource)
    with pytest.raises(CallerIdentityPermissionError):
        await _update(world, resource, actor_id="other-user")
    world.lock_repository.get_by_key.return_value = SimpleNamespace(
        id=7, holder_user_id="someone-else",
    )
    with pytest.raises(CallerLockEpochError):
        await _update(world, resource, lock_epoch=7)
    assert _persisted(world) == ("caller", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
async def test_admin_owner_transition_sync_failure_restores_caller(world, resource, caplog):
    _seed(world, resource)
    if resource == "mcp":
        world.mcp_sync.sync_mcp_identity_to_agent_principal.return_value = {"success": False}
        error = CallerMcpSyncError
    else:
        world.passport.update_passport.side_effect = RuntimeError("synthetic-secret")
        error = CallerCliSyncError
    with caplog.at_level(logging.INFO), pytest.raises(error):
        await _update(world, resource)
    assert _persisted(world) == ("caller", 2)
    modes = (world.repository.list_draft_call_types(1, "openclaw") if resource == "mcp"
             else world.repository.list_draft_cli_call_types(1, "openclaw"))
    assert set(modes.values()) == {McpCallType.CALLER}
    assert "compensated" in caplog.text
    assert "synthetic-secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
async def test_admin_does_not_downgrade_other_resource_caller(world, resource):
    _seed(world, "mcp")
    _seed(world, "cli")
    result = await _update(world, resource)
    assert result.bot_call_type is McpCallType.CALLER
    assert _persisted(world) == ("caller", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
async def test_admin_authorization_does_not_bypass_transaction_lock(world, resource):
    _seed(world, resource)
    # A lock appears after the service's optimistic unlocked check.
    with world.database.transactional_orm_session() as session:
        session.add(BotCollabLockModel(
            lock_key="bot:entity", holder_user_id="someone-else", env="dev",
        ))
    with pytest.raises(CallerLockEpochError):
        await _update(world, resource)
    assert _persisted(world) == ("caller", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["mcp", "cli"])
async def test_caller_request_does_not_consult_admin_membership(world, resource, monkeypatch):
    def unexpected_lookup():
        raise AssertionError("Caller requests must not acquire downgrade authority")

    monkeypatch.setattr(
        "agentclaw.community.core.access.admin_scopes.super_admin", unexpected_lookup,
    )
    _seed(world, resource)
    result = await _update(world, resource, call_type=McpCallType.CALLER)
    assert result.bot_call_type is McpCallType.CALLER
    assert _persisted(world) == ("caller", 1)


@pytest.mark.parametrize("resource", ["mcp", "cli"])
@pytest.mark.parametrize("authorized", [False, True])
def test_repository_final_transition_requires_explicit_service_authorization(world, resource, authorized):
    _seed(world, resource)
    args = dict(
        bot_pk=1, engine_type="openclaw", call_type=McpCallType.OWNER,
        modifier_id="owner", lock_key="bot:entity", lock_holder_user_id="owner",
        lock_epoch=None,
    )
    if authorized:
        args["allow_owner_transition"] = True
    if resource == "mcp":
        method = world.repository.replace_draft_call_type
        args.update(server_code="mcp", effective_server_codes={"mcp"})
    else:
        method = world.repository.replace_draft_cli_call_type
        args["cli_code"] = "custom-cli"
    if not authorized:
        with pytest.raises(CallerIdentityIrreversibleError):
            method(**args)
        assert _persisted(world) == ("caller", 0)
    else:
        assert method(**args).bot_call_type is McpCallType.OWNER
        assert _persisted(world) == ("owner", 1)


@pytest.mark.parametrize("field", ["allow_owner_transition", "is_super_admin", "actor_id"])
@pytest.mark.parametrize("schema_name", ["UpdateMcpCallTypeRequest", "UpdateMcpCallTypeQuery"])
def test_http_configuration_schemas_reject_spoofed_authorization(field, schema_name):
    from pydantic import ValidationError

    from agentclaw.community.adapters.http.caller_identity import schemas

    body = {field: True}
    if schema_name == "UpdateMcpCallTypeRequest":
        body["call_type"] = "owner"
    with pytest.raises(ValidationError) as exc:
        getattr(schemas, schema_name).model_validate(body)
    assert any(error["type"] == "extra_forbidden" for error in exc.value.errors())
