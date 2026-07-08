"""MCP market query service — read-only operations against MCP Center."""
from __future__ import annotations

from typing import Any, Optional

from injector import inject

from agentclaw.community.core.mcp.services.mcp_live_fetcher import fetch_tools_live
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin

logger = get_logger()


class MCPMarketService:
    """Wraps MCPCenterPlugin for market list, detail, and tenant queries."""

    @inject
    def __init__(self, mcp_center: MCPCenterPlugin) -> None:
        self.mcp_center = mcp_center

    def get_mcp_list(
        self,
        *,
        page_num: int = 1,
        page_size: int = 20,
        search_key: Optional[str] = None,
        server_codes: Optional[list[str]] = None,
        platform_server_codes: Optional[list[str]] = None,
        run_modes: Optional[list[str]] = None,
        statuses: Optional[list[str]] = None,
        transport_protocols: Optional[list[str]] = None,
        host_platforms: Optional[list[str]] = None,
        owners: Optional[list[str]] = None,
        network_types: Optional[list[str]] = None,
        categories: Optional[list[str]] = None,
        tenants: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Query MCP server list from MCP Center."""
        return self.mcp_center.get_mcp_list(
            page_num=page_num,
            page_size=page_size,
            search_key=search_key,
            server_codes=server_codes,
            platform_server_codes=platform_server_codes,
            run_modes=run_modes,
            statuses=statuses,
            transport_protocols=transport_protocols,
            host_platforms=host_platforms,
            owners=owners,
            network_types=network_types,
            categories=categories,
            tenants=tenants,
        )

    def get_mcp_detail(
        self,
        server_code: str,
        access_token: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Fetch single MCP server detail.

        If ``access_token`` is provided, attempts to fetch fresh ``tools/list``
        directly from the MCP server endpoint and merges it into the Center data.
        If the live fetch fails (network error, auth error, no endpoint, etc.),
        falls back to the cached Center data unchanged.
        """
        center_data = self.mcp_center.get_mcp_detail(server_code)
        if not center_data:
            return None

        if not access_token:
            return center_data

        live_tools = fetch_tools_live(center_data, access_token=access_token)
        if live_tools is not None:
            center_data["tools"] = live_tools
            logger.info(
                "[MCPMarketService] Replaced tools with live data for %s",
                server_code,
            )
        else:
            logger.info(
                "[MCPMarketService] Live fetch failed for %s, using Center data",
                server_code,
            )

        return center_data

    def get_tenant_list(
        self,
        *,
        tenant_code: Optional[str] = None,
        arch_domain_code: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get tenant list from MCP Center."""
        return self.mcp_center.get_tenant_list(
            tenant_code=tenant_code,
            arch_domain_code=arch_domain_code,
        )
