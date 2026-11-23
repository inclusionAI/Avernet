"""SkillSet control-plane conflicts keep their app-layer wire on the MCP write.

The internal unified-config write surfaces SkillSet state conflicts from both
stages of its flow: the capability read during validation, and the device sync.
Both used to end in the route handler's catch-all (via the sync wrapper's
``McpSyncFailedError`` conversion), answering ``500`` with the bare reason code.

``adapters.http.app._DOMAIN_ERROR_STATUS_MAP`` deliberately answers these
domain conflicts with 400 echoing the reason code clients already parse
(see its own comment). These tests pin the two passthrough fixes from either
side of the sync wrapper to that one-layer wire decision.
"""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.mcp.router import router as mcp_router
from agentclaw.community.api.mcp_config_service import MCPConfigServiceProtocol
from agentclaw.community.api.mcp_market_service import MCPMarketServiceProtocol
from agentclaw.community.api.mcp_sync_service import MCPSyncServiceProtocol
from agentclaw.community.core.errors import DomainError
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
)

_REASON = "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"


def _raise_conflict(*_args, **_kwargs):
    raise SkillSetControlPlaneConflictError(_REASON)


def _build_client(config_service, sync_service):
    # Same wiring the status-mapping tests use, so the wire is decided by the
    # production handlers rather than anything invented here.
    from agentclaw.community.adapters.http.app import (
        _domain_error_handler,
        _unhandled_exception_handler,
    )

    app = FastAPI()
    app.include_router(mcp_router)
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    market = MagicMock()
    market.get_mcp_detail.return_value = {"serverCode": "mcp.agent.search", "name": "Search"}

    class _TestModule(Module):
        def configure(self, binder):
            binder.bind(MCPConfigServiceProtocol, to=config_service)
            binder.bind(MCPMarketServiceProtocol, to=market)
            binder.bind(MCPSyncServiceProtocol, to=sync_service)

    attach_injector(app, Injector([_TestModule()]))
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="12345", staffId="12345", operatorName="alice"
    )
    return TestClient(app, raise_server_exceptions=False)


def _post_config(client):
    return client.post(
        "/api/mcp/user/config",
        json={
            "server_code": "mcp.agent.search",
            "api_key": "sk-abcdefghijklmnop",
        },
    )


def test_conflict_from_capability_read_answers_400_with_reason_code():
    # The production shape of CDO errorDetailId 1031066416: the capability
    # read inside validation raises the control-plane conflict.
    config_service = MagicMock()
    config_service.validate_headers_for_mcp.return_value = {"valid": True}
    config_service.validate_user_config_update.side_effect = _raise_conflict
    client = _build_client(config_service, MagicMock())

    resp = _post_config(client)

    assert resp.status_code == 400
    assert resp.json() == {"detail": _REASON}


async def _sync_ok(**_kwargs):
    return {"success": True, "sync_results": [], "error": None}


async def _sync_raises_conflict(**_kwargs):
    raise SkillSetControlPlaneConflictError(_REASON)


def test_conflict_from_device_sync_is_not_masked_as_mcp_sync_failure():
    # The sync-stage sibling: without the passthrough in config_flow the
    # conflict would arrive at the route wrapped as McpSyncFailedError.
    config_service = MagicMock()
    config_service.validate_user_config_update.return_value = {
        "valid": True,
        "affected_bot_ids": None,
        "affected_bot_owners": None,
    }
    config_service.update_user_unified_config.return_value = {"old": "config"}
    config_service.rollback_unified_config.return_value = None
    sync_service = MagicMock()
    sync_service.sync_mcp_detail_to_all_bots = _sync_raises_conflict
    client = _build_client(config_service, sync_service)

    resp = _post_config(client)

    assert resp.status_code == 400
    assert resp.json() == {"detail": _REASON}
    # The failed push must still roll the stored write back.
    assert config_service.rollback_unified_config.called


def test_happy_path_unchanged_when_no_conflict():
    config_service = MagicMock()
    config_service.validate_user_config_update.return_value = {
        "valid": True,
        "affected_bot_ids": None,
        "affected_bot_owners": None,
    }
    config_service.update_user_unified_config.return_value = {"old": "config"}
    config_service.rollback_unified_config.return_value = None
    sync_service = MagicMock()
    sync_service.sync_mcp_detail_to_all_bots = _sync_ok
    client = _build_client(config_service, sync_service)

    resp = _post_config(client)

    assert resp.status_code == 200
    assert resp.json()["success"] is True
