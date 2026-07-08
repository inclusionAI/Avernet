"""Tests for MCP API router."""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.mcp.router import router as mcp_router
from agentclaw.community.core.mcp.services.auth_service import MCPAuthService
from agentclaw.community.core.mcp.services.market_service import MCPMarketService


@pytest.fixture
def mock_market_service():
    return MagicMock(spec=MCPMarketService)


@pytest.fixture
def mock_auth_service():
    return MagicMock(spec=MCPAuthService)


@pytest.fixture
def client(mock_market_service, mock_auth_service):
    app = FastAPI()
    app.include_router(mcp_router)

    class _TestModule(Module):
        def configure(self, binder):
            binder.bind(MCPMarketService, to=mock_market_service)
            from agentclaw.community.api.mcp_market_service import MCPMarketServiceProtocol
            binder.bind(MCPMarketServiceProtocol, to=mock_market_service)
            binder.bind(MCPAuthService, to=mock_auth_service)
            from agentclaw.community.api.mcp_auth_service import MCPAuthServiceProtocol
            binder.bind(MCPAuthServiceProtocol, to=mock_auth_service)

    attach_injector(app, Injector([_TestModule()]))
    return TestClient(app, raise_server_exceptions=False)


# ==================== Router structure ====================

class TestMCPRouterImports:
    """Ensure MCP router loads without import errors and has expected routes."""

    def test_mcp_router_imports(self):
        from agentclaw.community.adapters.http.mcp.router import router
        assert router is not None
        assert len(router.routes) > 0

    def test_mcp_has_expected_routes(self):
        from agentclaw.community.adapters.http.mcp.router import router
        paths = {r.path for r in router.routes if hasattr(r, "path")}
        assert "/api/mcp/market/list" in paths
        assert "/api/mcp/market/detail" in paths
        assert "/api/mcp/market/permission" in paths
        assert "/api/mcp/market/permission/apply" in paths
        assert "/api/mcp/tenants" in paths
        assert "/api/mcp/user/config" in paths


# ==================== GET /api/mcp/market/list ====================

class TestListMCPServers:
    """Tests for GET /api/mcp/market/list"""

    def test_removes_ext_info_from_tool_input_schema(self, client, mock_market_service):
        mock_market_service.get_mcp_list.return_value = {
            "success": True,
            "data": [
                {
                    "serverCode": "mcp.test",
                    "tools": [
                        {
                            "name": "tool_a",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "extInfo": {
                                        "description": "secret defaults",
                                        "type": "object",
                                    },
                                    "query": {"type": "string"},
                                },
                            },
                        }
                    ],
                }
            ],
            "total": 1,
            "page_num": 1,
            "page_size": 10,
        }

        resp = client.get("/api/mcp/market/list")

        assert resp.status_code == 200
        properties = resp.json()["data"][0]["tools"][0]["inputSchema"]["properties"]
        assert "extInfo" not in properties
        assert properties["query"] == {"type": "string"}
        original_properties = mock_market_service.get_mcp_list.return_value["data"][0]["tools"][0]["inputSchema"][
            "properties"
        ]
        assert "extInfo" in original_properties


# ==================== GET /api/mcp/market/detail ====================

class TestGetMCPDetail:
    """Tests for GET /api/mcp/market/detail"""

    def test_success(self, client, mock_market_service):
        mock_data = {
            "serverCode": "mcp.test",
            "name": "Test MCP",
            "networkTypes": ["INTERNET"],
            "tools": [{"name": "tool_a"}],
        }
        mock_market_service.get_mcp_detail.return_value = mock_data

        resp = client.get("/api/mcp/market/detail?server_code=mcp.test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["serverCode"] == "mcp.test"
        mock_market_service.get_mcp_detail.assert_called_once_with("mcp.test", access_token=None)

    def test_removes_ext_info_from_tool_input_schema(self, client, mock_market_service):
        mock_data = {
            "serverCode": "mcp.test",
            "name": "Test MCP",
            "networkTypes": ["INTERNET"],
            "tools": [
                {
                    "name": "tool_a",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "extInfo": {
                                "description": "secret defaults",
                                "type": "object",
                            },
                            "query": {"type": "string"},
                        },
                    },
                }
            ],
        }
        mock_market_service.get_mcp_detail.return_value = mock_data

        resp = client.get("/api/mcp/market/detail?server_code=mcp.test")

        assert resp.status_code == 200
        properties = resp.json()["data"]["tools"][0]["inputSchema"]["properties"]
        assert "extInfo" not in properties
        assert properties["query"] == {"type": "string"}
        original_properties = mock_market_service.get_mcp_detail.return_value["tools"][0]["inputSchema"]["properties"]
        assert "extInfo" in original_properties

    def test_not_found_returns_404(self, client, mock_market_service):
        mock_market_service.get_mcp_detail.return_value = None

        resp = client.get("/api/mcp/market/detail?server_code=mcp.not.exist")

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_invalid_network_type_returns_404(self, client, mock_market_service):
        mock_data = {
            "serverCode": "mcp.bad",
            "networkTypes": ["INTRANET"],
        }
        mock_market_service.get_mcp_detail.return_value = mock_data

        resp = client.get("/api/mcp/market/detail?server_code=mcp.bad")

        assert resp.status_code == 404

    def test_missing_server_code_returns_422(self, client):
        resp = client.get("/api/mcp/market/detail")
        assert resp.status_code == 422

    def test_with_cookie_iam_token(self, client, mock_market_service, mock_auth_service):
        mock_data = {
            "serverCode": "mcp.test",
            "name": "Test MCP",
            "networkTypes": ["INTERNET"],
            "tools": [{"name": "tool_a"}],
        }
        mock_market_service.get_mcp_detail.return_value = mock_data
        mock_auth_service.exchange_iam_token.return_value = "cookie_token"

        resp = client.get(
            "/api/mcp/market/detail?server_code=mcp.test",
            cookies={"IAM_TOKEN": "cookie_iam_token"},
        )

        assert resp.status_code == 200
        mock_auth_service.exchange_iam_token.assert_called_once_with("cookie_iam_token")
        mock_market_service.get_mcp_detail.assert_called_once_with("mcp.test", access_token="cookie_token")
