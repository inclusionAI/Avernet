"""Re-export the MCP Header-group Service API owned by core/mcp."""

from __future__ import annotations

from agentclaw.community.core.mcp.scoped_config_contract import (
    MCPScopedConfigServiceProtocol,
)

__all__ = ["MCPScopedConfigServiceProtocol"]
