"""
MCPService Protocol — Model Context Protocol management interface.

Each engine implementation under engines/<name>/mcp.py provides a class that
structurally satisfies this Protocol. EngineManager exposes the active engine's
MCP plugin via `EngineManager.get_instance().mcp` (None if the engine does not
declare any MCP capabilities).

See src/engine/docs/heterogeneous-engine-architecture.md §6.1.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from engine.community.core.engine.context import AuthContext
from engine.community.core.mcp.models import (
    MCPFilterRequest,
    MCPFilterResult,
    MCPPrompt,
    MCPResource,
    MCPServer,
    MCPServerConfig,
    MCPServerStatus,
    MCPTool,
    MCPToolCallRequest,
    MCPToolCallResult,
)


@runtime_checkable
class MCPService(Protocol):
    """Backend talks to MCP-capable engines through this Protocol."""

    # ── Server configuration (CRUD) ──
    async def list_servers(
        self, auth: AuthContext | None = None,
    ) -> list[MCPServer]:
        """List all configured MCP servers, including status."""
        ...

    async def get_server(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> MCPServer | None:
        """Look up a single server by code."""
        ...

    async def create_server(
        self, config: MCPServerConfig, auth: AuthContext | None = None,
    ) -> MCPServer:
        """Register a new MCP server configuration."""
        ...

    async def update_server(
        self,
        server_code: str,
        config: MCPServerConfig,
        auth: AuthContext | None = None,
    ) -> MCPServer:
        """Update an existing server's configuration."""
        ...

    async def delete_server(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> bool:
        """Remove a server configuration. Returns True if it existed and was removed."""
        ...

    # ── Server lifecycle ──
    async def start_server(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> bool:
        """Start an MCP server. Returns True on successful start."""
        ...

    async def stop_server(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> bool:
        """Stop a running MCP server. Returns True on successful stop."""
        ...

    async def restart_server(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> bool:
        """Stop and then start an MCP server. Returns True on successful restart."""
        ...

    async def get_server_status(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> MCPServerStatus:
        """Report the current runtime status of an MCP server."""
        ...

    # ── Tools ──
    async def list_tools(
        self,
        server_code: str | None = None,
        auth: AuthContext | None = None,
    ) -> list[MCPTool]:
        """List tools exposed by a server, or across all servers when code is None."""
        ...

    async def call_tool(
        self, request: MCPToolCallRequest, auth: AuthContext | None = None,
    ) -> MCPToolCallResult:
        """Invoke a tool. Server resolution is automatic when request.server_code is None."""
        ...

    # ── Resources ──
    async def list_resources(
        self,
        server_code: str | None = None,
        auth: AuthContext | None = None,
    ) -> list[MCPResource]:
        """List resources exposed by a server, or across all servers when code is None."""
        ...

    async def read_resource(
        self, server_code: str, uri: str, auth: AuthContext | None = None,
    ) -> str:
        """Read a resource's content as a string."""
        ...

    # ── Prompts ──
    async def list_prompts(
        self,
        server_code: str | None = None,
        auth: AuthContext | None = None,
    ) -> list[MCPPrompt]:
        """List prompt templates exposed by a server, or across all servers when code is None."""
        ...

    async def get_prompt(
        self,
        server_code: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        auth: AuthContext | None = None,
    ) -> str:
        """Render a prompt template with the given arguments."""
        ...

    # ── Server allow-listing ──
    async def filter_servers(
        self, request: MCPFilterRequest, auth: AuthContext | None = None,
    ) -> MCPFilterResult:
        """Apply an allow-list of MCP servers — engines that wrap an external
        command (e.g. ``mcporter filter-servers``) surface stdout/stderr in
        the result; in-process engines may return empty diagnostics.
        """
        ...


__all__ = ["MCPService"]
