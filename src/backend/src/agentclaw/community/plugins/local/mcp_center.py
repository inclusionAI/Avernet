"""Local Noop ``MCPCenterPlugin``.

MCP Center is a remote service; local mode has no access. Read queries
return empty / no-permission defaults so callers degrade gracefully.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.NOOP,
    rationale="MCP Center is remote-only; local returns empty / no-permission",
)
class NoopMCPCenterPlugin(MockSeam, MCPCenterPlugin):
    def get_mcp_detail(self, server_code: str) -> dict[str, Any] | None:
        return None

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
        return {
            "success": True,
            "data": [],
            "total": 0,
            "page_num": page_num,
            "page_size": page_size,
        }

    def check_mcp_permission_detail(
        self, user_id: str, server_code: str
    ) -> dict[str, Any]:
        # Fail-open to match prod ``mcp_center.py`` exception path: blocking
        # all MCP access in local mode would silently disable every MCP skill.
        return {
            "has_permission": True,
            "access_level": "LOCAL",
            "tool_permissions": {},
        }

    def get_tenant_list(
        self,
        *,
        tenant_code: str | None = None,
        arch_domain_code: str | None = None,
    ) -> dict[str, Any]:
        return {"success": True, "data": [], "message": ""}
