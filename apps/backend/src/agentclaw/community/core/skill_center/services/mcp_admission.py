"""Pre-write admission checks for MCP SkillSet memberships."""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.config_compose.services.mcporter_composer import (
    McporterComposeError,
    mcp_network_priority_for,
    select_mcp_endpoint,
)
from agentclaw.community.core.mcp.mcp_config_service_protocol import (
    MCPConfigServiceProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    McpEndpointUnavailableError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin


def resolve_mcp_catalog_detail(
    mcp_center: MCPCenterPlugin, server_code: str
) -> dict[str, Any]:
    """Load one MCP Center record or reject the membership before it is written."""
    detail = mcp_center.get_mcp_detail(server_code)
    if not detail:
        raise SkillSetControlPlaneNotFoundError("MCP server not found")
    return detail


def require_mcp_delivery_eligibility(
    *,
    mcp_config: MCPConfigServiceProtocol,
    engine_type: str,
    owner_id: str,
    server_code: str,
    detail: dict[str, Any],
) -> None:
    """Reject a remote MCP that cannot be safely delivered to this runtime."""
    run_mode = detail.get("run_mode") or detail.get("runMode", "REMOTE")
    if run_mode == "LOCAL":
        return
    _, _, endpoint_env, transport_protocol = mcp_config.build_mcp_sync_payload(
        user_id=owner_id,
        mcp_data=detail,
        engine_type=engine_type,
    )
    try:
        select_mcp_endpoint(
            server_code,
            detail,
            endpoint_env,
            transport_protocol,
            mcp_network_priority_for(engine_type),
        )
    except McporterComposeError as exc:
        raise McpEndpointUnavailableError() from exc


def mcp_catalog_entry(server_code: str, detail: dict[str, Any]) -> dict[str, Any]:
    """Extract the membership display fields from resolved catalogue metadata."""
    return {
        "name": str(detail.get("name") or server_code),
        "description": detail.get("description"),
        "icon": detail.get("icon"),
    }
