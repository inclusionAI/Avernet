"""Capability Protocol for MCP auth: permission checks/applications + token exchange.

Vendor-neutral by contract — the corp implementation talks to internal identity/
permission services, while the community implementation is permissive (no remote
auth). Method signatures use neutral types only.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class MCPAuthPlugin(Plugin, Protocol):
    """Client for MCP Server / Tool permission checks and applications."""

    def check_permission(
        self,
        staff_no: str,
        service_code: str,
        tool_list: list[str] | None = None,
        is_public: bool = True,
    ) -> dict[str, bool]:
        """Check whether a user has permission for an MCP server/tool."""
        ...

    def apply_permission(
        self,
        staff_no: str,
        service_code: str,
        tool_list: list[str],
        is_public: bool = True,
        reason: str = "",
    ) -> dict[str, Any]:
        """Apply for MCP server/tool permission."""
        ...

    def query_permission_status(
        self,
        staff_no: str,
        service_code: str,
        tool_list: list[str] | None = None,
        is_public: bool = True,
    ) -> list[dict[str, Any]]:
        """Query permission status + in-progress application status."""
        ...

    def exchange_iam_token(self, subject_token: str) -> str | None:
        """Exchange a user's identity token for an MCP-auth access token.

        Returns ``None`` if the exchange fails (or when no exchange is needed).
        """
        ...
