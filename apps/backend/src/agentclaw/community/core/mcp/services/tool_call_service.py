"""Generic MCP tool-call service backed by the live HTTP transport."""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.mcp.mcp_tool_call_service_protocol import (
    MCPToolCallServiceProtocol,
)
from agentclaw.community.core.mcp.services.mcp_live_fetcher import call_tool_live


class MCPToolCallService(MCPToolCallServiceProtocol):
    """Invoke a named tool using endpoint metadata supplied by MCP Center."""

    def call_tool(
        self,
        *,
        server_detail: dict[str, Any],
        tool_name: str,
        arguments: dict[str, object],
        access_token: str | None = None,
    ) -> object | None:
        return call_tool_live(
            server_detail,
            tool_name=tool_name,
            arguments=arguments,
            access_token=access_token,
        )
