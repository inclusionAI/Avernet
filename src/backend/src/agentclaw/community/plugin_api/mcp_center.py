"""Protocol for querying MCP Center metadata and permissions.

Skill center services depend on this interface to fetch MCP details,
list MCP servers, and check user permissions — without coupling to
the concrete MCPService implementation.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class MCPCenterPlugin(Plugin, Protocol):
    """Read-only queries against MCP Center API."""

    def get_mcp_detail(self, server_code: str) -> dict[str, Any] | None:
        """Fetch full detail for a single MCP server.

        Args:
            server_code: Unique MCP server identifier.

        Returns:
            Dict with keys: name, description, icon, serverCode, accessLevel,
            tools, endpoints, runMode, etc.  None if not found.
        """
        ...

    def get_mcp_list(
        self,
        *,
        page_num: int = 1,
        page_size: int = 20,
        search_key: str | None = None,
        server_codes: list[str] | None = None,
        platform_server_codes: list[str] | None = None,
        run_modes: list[str] | None = None,
        statuses: list[str] | None = None,
        transport_protocols: list[str] | None = None,
        host_platforms: list[str] | None = None,
        owners: list[str] | None = None,
        network_types: list[str] | None = None,
        categories: list[str] | None = None,
        tenants: list[str] | None = None,
    ) -> dict[str, Any]:
        """Query MCP server list with filters.

        Args:
            page_num: Page number (1-based).
            page_size: Results per page.
            search_key: Fuzzy search keyword.
            server_codes: Filter by specific server codes.
            platform_server_codes: Filter by platform server codes.
            run_modes: Filter by run mode.
            statuses: Filter by status (e.g. ONLINE).
            transport_protocols: Filter by transport protocol.
            host_platforms: Filter by host platform.
            owners: Filter by owner staff numbers.
            network_types: Filter by network type (INTERNET/OFFICE/INTRANET).
            categories: Filter by category.
            tenants: Filter by tenant.

        Returns:
            Dict with keys: success, data (list of MCP dicts), total, page_num, page_size.
        """
        ...

    def check_mcp_permission_detail(
        self, user_id: str, server_code: str
    ) -> dict[str, Any]:
        """Check whether a user has permission for a specific MCP server.

        Args:
            user_id: Staff number / user identifier.
            server_code: MCP server code.

        Returns:
            Dict with keys: has_permission (bool), access_level (str),
            tool_permissions (dict mapping tool_name -> status).
        """
        ...

    def get_tenant_list(
        self,
        *,
        tenant_code: str | None = None,
        arch_domain_code: str | None = None,
    ) -> dict[str, Any]:
        """Get tenant list from MCP Center API.

        Args:
            tenant_code: Tenant code filter.
            arch_domain_code: Architecture domain code filter.

        Returns:
            Dict with keys: success, data, message.
        """
        ...
