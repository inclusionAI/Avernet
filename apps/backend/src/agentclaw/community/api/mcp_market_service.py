"""Service API Protocol for MCP marketplace listings.

Re-export only. The Protocol is defined in its owning core module
(``core/mcp/mcp_market_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.mcp.mcp_market_service_protocol import (
    MCPMarketServiceProtocol,
)

__all__ = [
    "MCPMarketServiceProtocol",
]
