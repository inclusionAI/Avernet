"""Service API Protocol for MCP permission + IAM token exchange."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MCPAuthServiceProtocol(Protocol):
    """Service API for MCP server permission checks + IAM token exchange."""

    def check_mcp_permission_detail(self, *args: Any, **kwargs: Any) -> Any: ...

    def apply_permission(self, *args: Any, **kwargs: Any) -> Any: ...

    def exchange_iam_token(self, subject_token: str) -> str | None: ...
