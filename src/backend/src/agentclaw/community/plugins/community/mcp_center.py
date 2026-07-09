"""Community ``MCPCenterPlugin`` implementation.

A real, deployable impl (not a ``MockSeam`` test double) backed by a local
config-file catalog. It implements the same ``MCPCenterPlugin`` interface the
corp HTTP plugin does, so ``core/mcp`` and ``core/skill_center`` resolve under
``DEPLOY_PROFILE=community`` with no internal MCP-Center service.

Behaviour:

- **Catalog** (``get_mcp_detail`` / ``get_mcp_list``) is served from a local
  registry file (same shape as ``configs/local-mcp-servers.yaml``). Empty by
  default — an operator may declare servers there, or stand up their own service
  behind this interface later.
- **Permission** (``check_mcp_permission_detail``) is allow-all: the community
  build has no permission service, and MCP permission only gates the marketplace
  "apply" UI, not tool execution.
- **Tenant** (``get_tenant_list``) returns a single default tenant.

The bot-run path uses bring-your-own MCP configs (``ac_user_mcp_config``) and
skill-set references, which sync to devices regardless of this catalog — so an
empty catalog never blocks a configured MCP server from working.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.mcp.services.local_mcp_registry import LocalMCPRegistry
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin


class CommunityMCPCenter(MCPCenterPlugin):
    """Config-file-backed MCP catalog with allow-all permission + default tenant."""

    _DEFAULT_TENANT: dict[str, Any] = {"code": "default", "name": "Community"}

    def __init__(self, registry_config_path: str | None = None) -> None:
        # No configured path ⇒ an EMPTY catalog. We deliberately do NOT fall back
        # to LocalMCPRegistry's default file (a corp/local-dev artifact in the
        # repo); a community catalog only exists when the operator points at one.
        self._registry = (
            LocalMCPRegistry(registry_config_path) if registry_config_path else None
        )

    def get_mcp_detail(self, server_code: str) -> dict[str, Any] | None:
        if self._registry is None:
            return None
        return self._registry.get_mcp_detail(server_code)

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
        items = (
            self._registry.list_mcp_details(
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
            if self._registry is not None
            else []
        )
        total = len(items)
        if page_size > 0:
            start = max(page_num - 1, 0) * page_size
            data = items[start: start + page_size]
        else:
            data = items
        return {
            "success": True,
            "data": data,
            "total": total,
            "page_num": page_num,
            "page_size": page_size,
        }

    def check_mcp_permission_detail(
        self, user_id: str, server_code: str
    ) -> dict[str, Any]:
        # Allow-all: no permission service in community; permission only gates the
        # marketplace "apply" UI, never tool execution.
        return {
            "has_permission": True,
            "access_level": "COMMUNITY",
            "tool_permissions": {},
        }

    def get_tenant_list(
        self,
        *,
        tenant_code: str | None = None,
        arch_domain_code: str | None = None,
    ) -> dict[str, Any]:
        return {"success": True, "data": [dict(self._DEFAULT_TENANT)], "message": ""}
