"""Rule #16 — MCP 市场 (/api/mcp) 契约测试。

验证 GET market/list, GET market/detail, GET market/permission,
GET /api/mcp/tenants 接口响应字段。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentclaw.community.core.mcp.services.market_service import MCPMarketService
from agentclaw.community.core.mcp.services.auth_service import MCPAuthService
from agentclaw.community.core.mcp.services.config_service import MCPConfigService

from tests.community.contracts.gateway.conftest import (
    assert_response_schema, assert_success, assert_has_fields,
    assert_response_data_contract, bind_mock_service,
)


MOCK_MCP_ITEM = {
    "serverCode": "test-mcp", "source": "internal",
    "name": "Test MCP", "icon": "", "description": "A test MCP server",
    "status": "ONLINE", "runMode": "remote", "hostPlatform": "",
    "platformServerCode": "", "hostAppName": "", "site": "", "tenant": "",
    "category": "", "accessLevel": "PUBLIC", "networkTypes": ["INTERNET"],
    "endpoints": [{
        "networkType": "INTERNET", "transportProtocol": "streamable-http",
        "env": "pre", "url": "https://mcp.example.com/sse", "headers": {},
    }],
    "stdioConfigs": None, "buCode": "", "productCode": "", "archDomainCode": "",
    "creator": {"userId": "448524", "userName": "TestUser"},
    "owner": {"userId": "448524", "userName": "TestUser"},
    "tools": [{"name": "tool_1", "description": "A tool"}],
    "tags": [], "codeRepoUrl": "", "launchChannels": [], "vendor": "",
}


class TestMcpMarketList:
    """GET /api/mcp/market/list — MCP 市场列表。"""

    def test_list_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        mock_svc = MagicMock(spec=MCPMarketService)
        mock_svc.get_mcp_list.return_value = {
            "success": True, "data": [MOCK_MCP_ITEM],
            "total": 1, "page_num": 1, "page_size": 20,
        }
        bind_mock_service(MCPMarketService, mock_svc, app_with_testing_modules)

        resp = gw_client.get("/api/mcp/market/list", params={
            "page_num": 1, "page_size": 20,
        })
        body = resp.json()

        assert_success(body, "GET /api/mcp/market/list")
        assert_response_data_contract(body, "rule16_GET_api_mcp_market_list", update=contract_snapshot_update)
        data = body.get("data", {})
        assert isinstance(data, list), f"GET /api/mcp/market/list data should be list, got {type(data).__name__}"
        assert_has_fields(
            data[0],
            {"serverCode": str, "name": str, "description": str, "status": str,
             "accessLevel": str, "networkTypes": list, "tools": list},
            label="GET /api/mcp/market/list data[0]",
        )


class TestMcpPermission:
    """GET /api/mcp/market/permission — MCP 权限查询。

    注意: MCPPermissionResponse 将 has_permission / access_level 放在响应顶层，
    而非嵌套在 data 下。
    """

    def test_permission_schema(self, gw_client, app_with_testing_modules):
        mock_svc = MagicMock(spec=MCPAuthService)
        mock_svc.check_mcp_permission_detail.return_value = {
            "has_permission": True,
            "access_level": "PUBLIC",
            "tool_permissions": {},
        }
        bind_mock_service(MCPAuthService, mock_svc, app_with_testing_modules)

        resp = gw_client.get("/api/mcp/market/permission", params={
            "server_code": "test-mcp", "user_id": "448524",
        })
        body = resp.json()

        assert_success(body, "GET /api/mcp/market/permission")
        assert_has_fields(
            body, {"has_permission": bool, "access_level": str},
            label="GET /api/mcp/market/permission",
        )


class TestMcpTenants:
    """GET /api/mcp/tenants — MCP 租户查询。"""

    def test_tenants_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        mock_svc = MagicMock(spec=MCPMarketService)
        mock_svc.get_tenant_list.return_value = {
            "success": True,
            "data": [{
                "site": "default", "archDomainCode": "domain_001",
                "archDomainName": "Test Domain", "code": "tenant_001",
                "name": "Test Tenant", "categories": [],
            }],
        }
        bind_mock_service(MCPMarketService, mock_svc, app_with_testing_modules)

        resp = gw_client.get("/api/mcp/tenants")
        body = resp.json()

        assert_success(body, "GET /api/mcp/tenants")
        assert_response_data_contract(body, "rule16_GET_api_mcp_tenants", update=contract_snapshot_update)
        data = body.get("data", {})
        assert isinstance(data, list), f"GET /api/mcp/tenants data should be list, got {type(data).__name__}"
        assert_has_fields(
            data[0], {"code": str, "name": str, "categories": list},
            label="GET /api/mcp/tenants data[0]",
        )
