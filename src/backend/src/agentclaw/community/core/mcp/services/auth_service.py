"""MCP auth orchestration service — permission checks and applications."""
from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.mcp_auth import MCPAuthPlugin
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin


logger = get_logger()


def _is_local_mcp(mcp_data: dict[str, Any]) -> bool:
    return (
        mcp_data.get("source") == "local"
        or mcp_data.get("accessLevel") == "LOCAL"
        or mcp_data.get("runMode") == "LOCAL"
        or mcp_data.get("run_mode") == "LOCAL"
    )


class MCPAuthService:
    """Combines MCPCenter metadata with MCPAuthPlugin for permission flows."""

    @inject
    def __init__(
        self,
        auth_client: MCPAuthPlugin,
        mcp_center: MCPCenterPlugin,
    ) -> None:
        self.auth_client = auth_client
        self.mcp_center = mcp_center

    def check_mcp_permission_detail(
        self, user_id: str, server_code: str
    ) -> dict[str, Any]:
        """Check user permission for an MCP server and its tools.

        1. Fetch server detail from MCP Center (accessLevel, tools).
        2. Query auth status from MCPAuthPlugin.
        3. Merge results into a unified permission view.
        """
        try:
            mcp_data = self.mcp_center.get_mcp_detail(server_code)
            if not mcp_data:
                return {
                    "has_permission": False,
                    "access_level": "",
                    "tool_permissions": {},
                }

            access_level = mcp_data.get("accessLevel", "")
            if _is_local_mcp(mcp_data):
                return {
                    "has_permission": True,
                    "access_level": access_level or "LOCAL",
                    "tool_permissions": {},
                }
            is_public = access_level == "PUBLIC"
            tools = mcp_data.get("tools", []) or []
            tool_names = [
                t["name"] for t in tools if isinstance(t, dict) and t.get("name")
            ]
        except Exception as e:
            logger.error("[MCPAuthService] MCP Center error for %s: %s", server_code, e)
            return {
                "has_permission": True,
                "access_level": "",
                "tool_permissions": {},
            }

        status_list = self.auth_client.query_permission_status(
            staff_no=user_id,
            service_code=server_code,
            tool_list=tool_names if tool_names else None,
            is_public=is_public,
        )

        has_permission = False
        tool_permissions: dict[str, Any] = {}

        for item in status_list:
            rc = item.get("resource_code", "")
            state = item.get("auth_state", "UNAUTHORIZED")
            if rc == server_code:
                has_permission = state == "AUTHORIZED"
            elif "/" in rc:
                tool_name = rc.split("/", 1)[1]
                tool_permissions[tool_name] = {
                    "code": state,
                    "name": item.get("auth_state_name", state),
                }

        return {
            "has_permission": has_permission,
            "access_level": access_level,
            "tool_permissions": tool_permissions,
        }

    def apply_permission(
        self,
        staff_no: str,
        service_code: str,
        tool_list: list[str],
        is_public: bool = True,
        reason: str = "",
    ) -> dict[str, Any]:
        """Apply for MCP server/tool permission."""
        try:
            mcp_data = self.mcp_center.get_mcp_detail(service_code)
            if mcp_data and _is_local_mcp(mcp_data):
                return {"success": True, "process_url": None, "error": None}
        except Exception as e:
            logger.error("[MCPAuthService] MCP Center error for %s: %s", service_code, e)
        return self.auth_client.apply_permission(
            staff_no=staff_no,
            service_code=service_code,
            tool_list=tool_list,
            is_public=is_public,
            reason=reason,
        )

    def exchange_iam_token(self, subject_token: str) -> str | None:
        """Exchange a user's identity token for an MCP-auth access token."""
        return self.auth_client.exchange_iam_token(subject_token)
