"""Community ``MCPAuthPlugin`` implementation — permissive (no remote auth).

A real, deployable impl (not a ``MockSeam`` test double). The community build has
no MCP permission/IAM service; MCP permission only gates the marketplace "apply"
UI, never tool execution. So every check is granted and token exchange is a no-op.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.plugin_api.mcp_auth import MCPAuthPlugin


def _authorized(resource_code: str) -> dict[str, Any]:
    return {
        "resource_code": resource_code,
        "auth_state": "AUTHORIZED",
        "auth_state_name": "Authorized",
        "permission_code": "",
        "resource_name": "",
        "process_url": None,
        "create_date": None,
    }


class CommunityMCPAuthPlugin(MCPAuthPlugin):
    """Allow-all MCP auth for the community profile."""

    def check_permission(
        self,
        staff_no: str,
        service_code: str,
        tool_list: list[str] | None = None,
        is_public: bool = True,
    ) -> dict[str, bool]:
        result: dict[str, bool] = {service_code: True}
        for tool in tool_list or []:
            result[tool] = True
        return result

    def apply_permission(
        self,
        staff_no: str,
        service_code: str,
        tool_list: list[str],
        is_public: bool = True,
        reason: str = "",
    ) -> dict[str, Any]:
        return {"success": True, "process_url": None, "error": None}

    def query_permission_status(
        self,
        staff_no: str,
        service_code: str,
        tool_list: list[str] | None = None,
        is_public: bool = True,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = [_authorized(service_code)]
        for tool in tool_list or []:
            results.append(_authorized(f"{service_code}/{tool}"))
        return results

    def exchange_iam_token(self, subject_token: str) -> str | None:
        # No IAM exchange in community — callers fall back to the inbound token.
        return None
