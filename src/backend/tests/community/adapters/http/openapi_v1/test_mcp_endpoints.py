"""Endpoint tests for the public ``/openapi/v1/bots/mcp`` API (Track B).

A minimal FastAPI app hosts the mcp router with the caller principal overridden
and the four MCP services bound to mocks via the injector — mirroring the bots
endpoint harness. The real authenticator stays a stub; ``require_principal`` is
overridden per test.

Note: a bare ``FastAPI()`` does not install the app-level 422→envelope handler
(that is wired in ``adapters/http/app.py`` and tested surface-wide by
``test_validation_envelope.py``), so body-validation cases here assert the 422
status, not the envelope shape.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.mcp.router import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.mcp_auth_service import MCPAuthServiceProtocol
from agentclaw.community.api.mcp_config_service import MCPConfigServiceProtocol
from agentclaw.community.api.mcp_market_service import MCPMarketServiceProtocol
from agentclaw.community.api.mcp_sync_service import MCPSyncServiceProtocol


def _server(**ov):
    base = {
        "serverCode": "mcp.weather",
        "name": "Weather",
        "description": "d",
        "networkTypes": ["INTERNET"],
        "transportProtocol": "SSE",
        "tools": [
            {"name": "get", "inputSchema": {"properties": {"extInfo": {"z": 1}, "q": 2}}}
        ],
    }
    base.update(ov)
    return base


@pytest.fixture
def market():
    m = MagicMock()
    m.get_mcp_list.return_value = {"success": True, "data": [_server()], "total": 1}
    m.get_mcp_detail.return_value = _server()
    m.get_tenant_list.return_value = {
        "success": True,
        "data": [{"code": "t1", "name": "Tenant 1", "categories": [{"name": "cat-a"}]}],
    }
    return m


@pytest.fixture
def auth():
    m = MagicMock()
    m.check_mcp_permission_detail.return_value = {
        "has_permission": True,
        "access_level": "PUBLIC",
        "tool_permissions": {"get": {"code": "AUTHORIZED"}},
    }
    return m


@pytest.fixture
def config():
    m = MagicMock()
    m.get_user_unified_config.return_value = None
    m.validate_headers_for_mcp.return_value = {"valid": True, "error": None}
    m.update_user_unified_config.return_value = None
    m.rollback_unified_config.return_value = None
    return m


@pytest.fixture
def sync():
    m = MagicMock()
    m.sync_mcp_detail_to_all_bots = AsyncMock(
        return_value={"success": True, "sync_results": [], "error": None}
    )
    return m


@pytest.fixture
def client(market, auth, config, sync):
    class _M(Module):
        def configure(self, binder):
            binder.bind(MCPMarketServiceProtocol, to=market)
            binder.bind(MCPAuthServiceProtocol, to=auth)
            binder.bind(MCPConfigServiceProtocol, to=config)
            binder.bind(MCPSyncServiceProtocol, to=sync)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u1"}
    attach_injector(app, Injector([_M()]))
    return TestClient(app)


def _ok(resp, code=200000):
    body = resp.json()
    assert resp.status_code == 200, body
    assert body["code"] == code, body
    assert "request_id" in body
    return body["data"]


# ── marketplace ─────────────────────────────────────────────────────


def test_list_servers_maps_fields_and_omits_tools(client):
    data = _ok(client.get("/openapi/v1/bots/mcp/servers"))
    assert data["total"] == 1
    item = data["items"][0]
    assert item["server_code"] == "mcp.weather"
    assert item["network_types"] == ["INTERNET"]
    # The list is the lightweight McpServer projection — tools (and any extInfo
    # they carry) are not exposed here; that is the detail endpoint's job.
    assert "tools" not in item


def test_list_servers_forwards_keyword_and_paging(client, market):
    client.get("/openapi/v1/bots/mcp/servers?keyword=rain&page=3&page_size=7")
    kw = market.get_mcp_list.call_args.kwargs
    assert kw["search_key"] == "rain"
    assert kw["page_num"] == 3 and kw["page_size"] == 7
    assert kw["network_types"] == ["INTERNET", "OFFICE"]


def test_list_servers_upstream_failure_is_502(client, market):
    market.get_mcp_list.return_value = {"success": False, "message": "boom"}
    resp = client.get("/openapi/v1/bots/mcp/servers")
    assert resp.status_code == 502
    assert resp.json()["message"] == "MCP service error"


def test_list_tenants(client):
    data = _ok(client.get("/openapi/v1/bots/mcp/tenants"))
    assert data[0]["code"] == "t1"
    assert data[0]["categories"] == ["cat-a"]


def test_tenants_upstream_failure_is_502(client, market):
    market.get_tenant_list.return_value = {"success": False}
    resp = client.get("/openapi/v1/bots/mcp/tenants")
    assert resp.status_code == 502


def test_server_detail_strips_ext_info(client):
    data = _ok(client.get("/openapi/v1/bots/mcp/servers/mcp.weather"))
    assert data["server_code"] == "mcp.weather"
    props = data["tools"][0]["inputSchema"]["properties"]
    assert "extInfo" not in props and props["q"] == 2


def test_unknown_server_is_404_not_found(client, market):
    market.get_mcp_detail.return_value = None
    resp = client.get("/openapi/v1/bots/mcp/servers/nope")
    assert resp.status_code == 404
    assert resp.json()["message"] == "Not found"


def test_invisible_server_is_identical_404(client, market):
    """A network-type-hidden server answers the SAME body as an unknown one."""
    market.get_mcp_detail.return_value = None
    r_unknown = client.get("/openapi/v1/bots/mcp/servers/nope").json()
    market.get_mcp_detail.return_value = _server(networkTypes=["SECRET"])
    r_hidden = client.get("/openapi/v1/bots/mcp/servers/secret").json()
    assert r_unknown == r_hidden  # cannot probe existence


# ── permission ──────────────────────────────────────────────────────


def test_permission_uses_caller_identity_only(client, auth):
    # Even with a spoofed ?user_id=, the service is queried for the principal.
    _ok(client.get("/openapi/v1/bots/mcp/servers/mcp.weather/permissions?user_id=evil"))
    args = auth.check_mcp_permission_detail.call_args.args
    assert args[0] == "u1"  # owner from principal, not the query param


def test_permission_shape(client):
    data = _ok(client.get("/openapi/v1/bots/mcp/servers/mcp.weather/permissions"))
    assert data["has_access"] is True
    assert data["access_level"] == "PUBLIC"
    assert data["tool_permissions"]["get"]["code"] == "AUTHORIZED"


# ── config read ─────────────────────────────────────────────────────


def test_get_config_absent_reports_no_config(client):
    data = _ok(client.get("/openapi/v1/bots/mcp/servers/mcp.weather/config"))
    assert data["has_config"] is False
    assert data["endpoint_env"] == "PROD"
    assert data["api_key"] is None


def test_get_config_masks_long_key(client, config):
    config.get_user_unified_config.return_value = {
        "api_key": "sk-abcdefghijklmnop",
        "headers": {"h": "v"},
        "endpoint_env": "PRE",
        "transport_protocol": "SSE",
    }
    data = _ok(client.get("/openapi/v1/bots/mcp/servers/mcp.weather/config"))
    assert data["api_key"] == "sk-a****mnop"
    assert data["has_config"] is True
    assert data["endpoint_env"] == "PRE"


def test_get_config_masks_short_key(client, config):
    config.get_user_unified_config.return_value = {"api_key": "sk-1", "headers": {}}
    data = _ok(client.get("/openapi/v1/bots/mcp/servers/mcp.weather/config"))
    assert data["api_key"] == "****"


def test_raw_key_never_appears_in_response_text(client, config):
    config.get_user_unified_config.return_value = {"api_key": "sk-abcdefghijklmnop"}
    resp = client.get("/openapi/v1/bots/mcp/servers/mcp.weather/config")
    assert "sk-abcdefghijklmnop" not in resp.text


# ── config write ────────────────────────────────────────────────────


def test_put_config_success_returns_reread_state(client, config):
    # After the write, the handler re-reads for the response.
    config.get_user_unified_config.return_value = {
        "api_key": "sk-abcdefghijklmnop",
        "headers": {"h": "v"},
        "endpoint_env": "PROD",
        "transport_protocol": "SSE",
    }
    data = _ok(
        client.put(
            "/openapi/v1/bots/mcp/servers/mcp.weather/config",
            json={"api_key": "sk-abcdefghijklmnop", "endpoint_env": "PROD"},
        )
    )
    assert data["api_key"] == "sk-a****mnop"  # masked, from re-read
    assert data["has_config"] is True


def test_put_config_scopes_write_to_caller(client, config, sync):
    client.put(
        "/openapi/v1/bots/mcp/servers/mcp.weather/config",
        json={"api_key": "sk-1"},
    )
    assert config.update_user_unified_config.call_args.kwargs["user_id"] == "u1"
    assert sync.sync_mcp_detail_to_all_bots.call_args.kwargs["entity_id"] == "u1"


def test_put_config_sync_failure_is_502_and_rolls_back(client, config, sync):
    sync.sync_mcp_detail_to_all_bots.return_value = {
        "success": False,
        "error": "device down",
        "sync_results": [],
    }
    resp = client.put(
        "/openapi/v1/bots/mcp/servers/mcp.weather/config",
        json={"api_key": "sk-1"},
    )
    assert resp.status_code == 502
    assert resp.json()["message"] == "Device sync failed"
    config.rollback_unified_config.assert_called_once()


def test_put_config_unknown_server_is_404(client, market):
    market.get_mcp_detail.return_value = None
    resp = client.put(
        "/openapi/v1/bots/mcp/servers/nope/config", json={"api_key": "sk-1"}
    )
    assert resp.status_code == 404


def test_put_config_rejects_sync_mode_as_422(client):
    resp = client.put(
        "/openapi/v1/bots/mcp/servers/mcp.weather/config",
        json={"sync_mode": "single"},
    )
    assert resp.status_code == 422


def test_put_config_rejects_dev_endpoint_env_as_422(client):
    resp = client.put(
        "/openapi/v1/bots/mcp/servers/mcp.weather/config",
        json={"endpoint_env": "DEV"},
    )
    assert resp.status_code == 422


# ── auth ────────────────────────────────────────────────────────────


def test_missing_principal_is_401(client):
    client.app.dependency_overrides[require_principal] = lambda: None
    resp = client.get("/openapi/v1/bots/mcp/servers")
    assert resp.status_code == 401
    assert resp.json()["code"] == 401000
