"""Request-local CLI identities survive sparse owner-row deletion."""

from copy import deepcopy
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.caller_identity.contracts import (
    CallerCliSyncError,
    CliCallTypeMutationResult,
    McpCallType,
)
from agentclaw.community.core.caller_identity.service import CallerIdentityService
from agentclaw.community.core.mcp.services.cli_passport_scope import CliPassportScopeReconciler


def _world(cli_code: str, *, explicit_caller: bool = True) -> SimpleNamespace:
    bot = {
        "id": 1, "bot_id": "bot-1", "owner_id": "owner-1",
        "bot_type": "service", "status": "ACTIVE", "active_engine": "openclaw",
        "entity_id": "entity-1", "entity_type": "staff", "call_type": "caller",
    }
    sparse = {"local-cli": McpCallType.CALLER}
    if explicit_caller:
        sparse[cli_code] = McpCallType.CALLER
    passport_state = {
        "clis": [
            {"cli_code": cli_code, "identity_mode": "caller", "cli_name": "Target", "cli_desc": "Description"},
            {"cli_code": "historical-cli", "identity_mode": "caller"},
            {"cli_code": "local-cli", "identity_mode": "owner"},
            {"cli_code": "owner-cli", "identity_mode": "owner"},
        ],
        "mcps": [
            {"mcp_code": "historical-mcp", "identity_mode": "caller", "mcp_name": "Retained"},
            {"mcp_code": "local-mcp", "identity_mode": "owner"},
            {"mcp_code": "owner-mcp", "identity_mode": "owner"},
        ],
        "credentials": {"token": "synthetic-private-value"},
    }
    repository = MagicMock()
    repository.list_draft_cli_call_types.side_effect = lambda *_: sparse
    repository.list_draft_call_types.return_value = {"local-mcp": McpCallType.CALLER}

    def replace(**kwargs):
        previous = sparse.get(cli_code)
        if kwargs["call_type"] is McpCallType.OWNER:
            sparse.pop(cli_code, None)
        else:
            sparse[cli_code] = kwargs["call_type"]
        return CliCallTypeMutationResult(
            previous_explicit_call_type=previous, revision=2,
            bot_call_type=McpCallType.CALLER, caller_config_revision=3,
        )

    def compensate(**kwargs):
        sparse[cli_code] = kwargs["previous_explicit_call_type"]
        return True

    repository.replace_draft_cli_call_type.side_effect = replace
    repository.compensate_draft_cli_call_type.side_effect = compensate
    passport = MagicMock()
    passport.query_agent_passport.side_effect = lambda *_: deepcopy(passport_state)
    reconciler = CliPassportScopeReconciler(passport_plugin=passport, identity_repository=repository)
    bot_repository = MagicMock()
    bot_repository.get_by_id_and_entity.return_value = bot
    lock_repository = MagicMock()
    lock_repository.get_by_key.return_value = None
    mcp_provider = MagicMock()
    mcp_provider.collect_bot_active_mcps.return_value = [{"server_code": "local-mcp"}]
    service = CallerIdentityService(
        bot_repository=bot_repository, collaborator_repository=MagicMock(),
        lock_repository=lock_repository, mcp_provider=mcp_provider,
        repository=repository, mcp_sync_service=MagicMock(),
        passport_plugin=passport, cli_scope_reconciler=reconciler,
    )
    return SimpleNamespace(
        bot=bot, sparse=sparse, passport_state=passport_state, repository=repository,
        passport=passport, reconciler=reconciler, service=service,
    )


async def _update(world, cli_code, mode):
    return await world.service.update_cli_call_type(
        bot_id="bot-1", cli_code=cli_code, call_type=mode,
        actor_id="owner-1", entity_id="entity-1",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cli_code", ["dataphin", "deepinsight-cli", "custom-cli"])
@pytest.mark.parametrize("explicit_caller", [True, False])
async def test_owner_intent_replaces_history_preserving_complete_scope(cli_code, explicit_caller, caplog):
    world = _world(cli_code, explicit_caller=explicit_caller)
    with caplog.at_level(logging.INFO):
        result = await _update(world, cli_code, McpCallType.OWNER)

    assert result.call_type is McpCallType.OWNER
    assert result.bot_call_type is McpCallType.CALLER
    assert cli_code not in world.sparse
    scope = world.passport.update_passport.call_args.kwargs["resource_scope"]
    clis = {item["cli_code"]: item for item in scope["cli_items"]}
    assert clis[cli_code] == {
        "cli_code": cli_code, "identity_mode": "owner", "cli_name": "Target", "cli_desc": "Description",
    }
    assert clis["historical-cli"]["identity_mode"] == "caller"
    assert clis["local-cli"]["identity_mode"] == "caller"
    assert clis["owner-cli"]["identity_mode"] == "owner"
    assert scope["mcp_items"] == [
        {"mcp_code": "historical-mcp", "identity_mode": "caller", "mcp_name": "Retained"},
        {"mcp_code": "local-mcp", "identity_mode": "caller"},
        {"mcp_code": "owner-mcp", "identity_mode": "owner"},
    ]
    assert scope["mcp_codes"] == ["historical-mcp", "local-mcp", "owner-mcp"]
    assert "requested_cli_identity_modes=" in caplog.text
    assert "cli_identity_modes=" in caplog.text
    assert f"'{cli_code}': 'owner'" in caplog.text
    assert "agentpass_cli_scope_update_succeeded" in caplog.text
    assert "cli_call_type_update_succeeded" in caplog.text
    assert "synthetic-private-value" not in caplog.text


@pytest.mark.asyncio
async def test_owner_to_caller_still_updates_passport():
    world = _world("custom-cli", explicit_caller=False)
    world.passport_state["clis"][0]["identity_mode"] = "owner"
    result = await _update(world, "custom-cli", McpCallType.CALLER)
    assert result.call_type is McpCallType.CALLER
    assert world.sparse["custom-cli"] is McpCallType.CALLER
    assert world.passport.update_passport.call_args.kwargs["resource_scope"]["cli_items"][0]["identity_mode"] == "caller"


def test_bootstrap_without_intent_keeps_historical_caller():
    world = _world("custom-cli", explicit_caller=False)
    world.reconciler.reconcile(bot=world.bot)
    assert world.passport.update_passport.call_args.kwargs["resource_scope"]["cli_items"][0]["identity_mode"] == "caller"


def test_explicit_intent_wins_without_mutating_repository_mapping():
    world = _world("custom-cli")
    before = dict(world.sparse)
    world.reconciler.reconcile(bot=world.bot, requested_cli_identity_modes={"custom-cli": "owner"})
    assert world.sparse == before
    assert world.passport.update_passport.call_args.kwargs["resource_scope"]["cli_items"][0]["identity_mode"] == "owner"


@pytest.mark.asyncio
async def test_failed_owner_sync_restores_sparse_caller_and_logs_no_secret(caplog):
    world = _world("custom-cli")
    world.passport.update_passport.side_effect = RuntimeError("synthetic-private-value")
    with caplog.at_level(logging.INFO), pytest.raises(CallerCliSyncError):
        await _update(world, "custom-cli", McpCallType.OWNER)
    assert world.sparse["custom-cli"] is McpCallType.CALLER
    world.repository.compensate_draft_cli_call_type.assert_called_once()
    compensation = world.repository.compensate_draft_cli_call_type.call_args.kwargs
    assert compensation["previous_explicit_call_type"] is McpCallType.CALLER
    assert compensation["expected_revision"] == 2
    assert compensation["expected_caller_config_revision"] == 3
    assert world.passport.update_passport.call_args.kwargs["resource_scope"]["cli_items"][0]["identity_mode"] == "owner"
    for event in ("agentpass_cli_scope_update_requested", "agentpass_cli_scope_update_failed", "cli_call_type_update_compensated", "cli_call_type_update_failed"):
        assert event in caplog.text
    assert "RuntimeError" in caplog.text
    assert "cli_call_type_update_succeeded" not in caplog.text
    assert "agentpass_cli_scope_update_succeeded" not in caplog.text
    assert "synthetic-private-value" not in caplog.text
