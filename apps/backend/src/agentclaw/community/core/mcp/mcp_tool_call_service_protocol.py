"""Service API protocol for invoking one tool on an MCP server."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MCPToolCallServiceProtocol(Protocol):
    """Invoke MCP tools without exposing transport details to product domains."""

    def call_tool(
        self,
        *,
        server_detail: dict[str, Any],
        tool_name: str,
        arguments: dict[str, object],
        access_token: str | None = None,
    ) -> object | None: ...
