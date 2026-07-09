"""Tests for MCPMarketService."""
from unittest.mock import MagicMock, patch

from agentclaw.community.core.mcp.services.market_service import MCPMarketService


class TestMCPMarketService:
    """MCPMarketService delegates read-only queries to MCPCenterPlugin."""

    def test_get_mcp_list_delegates_to_plugin(self):
        mock_center = MagicMock()
        mock_center.get_mcp_list.return_value = {
            "success": True,
            "data": [{"serverCode": "mcp.test"}],
            "total": 1,
            "page_num": 1,
            "page_size": 20,
        }
        svc = MCPMarketService(mcp_center=mock_center)
        result = svc.get_mcp_list(page_num=1, page_size=10, search_key="test")
        assert result["success"] is True
        mock_center.get_mcp_list.assert_called_once_with(
            page_num=1,
            page_size=10,
            search_key="test",
            server_codes=None,
            platform_server_codes=None,
            run_modes=None,
            statuses=None,
            transport_protocols=None,
            host_platforms=None,
            owners=None,
            network_types=None,
            categories=None,
            tenants=None,
        )

    def test_get_mcp_detail_delegates_to_plugin(self):
        mock_center = MagicMock()
        mock_center.get_mcp_detail.return_value = {"serverCode": "mcp.test", "name": "Test MCP"}
        svc = MCPMarketService(mcp_center=mock_center)
        result = svc.get_mcp_detail("mcp.test")
        assert result["serverCode"] == "mcp.test"
        mock_center.get_mcp_detail.assert_called_once_with("mcp.test")

    def test_get_mcp_detail_with_live_fetch_success(self):
        """When access_token provided and live fetch succeeds, tools are replaced."""
        mock_center = MagicMock()
        mock_center.get_mcp_detail.return_value = {
            "serverCode": "mcp.test",
            "name": "Test MCP",
            "tools": [{"name": "old_tool"}],
            "endpoints": [
                {"networkType": "INTERNET", "url": "https://example.com/mcp", "transportProtocol": "STREAMABLE_HTTP"},
            ],
        }
        svc = MCPMarketService(mcp_center=mock_center)

        with patch("agentclaw.community.core.mcp.services.market_service.fetch_tools_live", return_value=[{"name": "live_tool"}]):
            result = svc.get_mcp_detail("mcp.test", access_token="test_token")

        assert result["tools"] == [{"name": "live_tool"}]
        mock_center.get_mcp_detail.assert_called_once_with("mcp.test")

    def test_get_mcp_detail_with_live_fetch_failure_falls_back(self):
        """When live fetch fails, falls back to Center data unchanged."""
        mock_center = MagicMock()
        mock_center.get_mcp_detail.return_value = {
            "serverCode": "mcp.test",
            "name": "Test MCP",
            "tools": [{"name": "old_tool"}],
        }
        svc = MCPMarketService(mcp_center=mock_center)

        with patch("agentclaw.community.core.mcp.services.market_service.fetch_tools_live", return_value=None):
            result = svc.get_mcp_detail("mcp.test", access_token="test_token")

        assert result["tools"] == [{"name": "old_tool"}]

    def test_get_mcp_detail_without_access_token_skips_live_fetch(self):
        """Without access_token, no live fetch attempt is made."""
        mock_center = MagicMock()
        mock_center.get_mcp_detail.return_value = {
            "serverCode": "mcp.test",
            "name": "Test MCP",
            "tools": [{"name": "old_tool"}],
        }
        svc = MCPMarketService(mcp_center=mock_center)

        with patch("agentclaw.community.core.mcp.services.market_service.fetch_tools_live") as mock_fetch:
            result = svc.get_mcp_detail("mcp.test")

        assert result["tools"] == [{"name": "old_tool"}]
        mock_fetch.assert_not_called()

    def test_get_tenant_list_delegates_to_plugin(self):
        mock_center = MagicMock()
        mock_center.get_tenant_list.return_value = {"success": True, "data": []}
        svc = MCPMarketService(mcp_center=mock_center)
        result = svc.get_tenant_list(tenant_code="INNER_DEFAULT_TENANT")
        assert result["success"] is True
        mock_center.get_tenant_list.assert_called_once_with(
            tenant_code="INNER_DEFAULT_TENANT",
            arch_domain_code=None,
        )
