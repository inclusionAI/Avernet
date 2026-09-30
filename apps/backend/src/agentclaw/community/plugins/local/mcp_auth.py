"""Local MCPAuthPlugin — returns mock permissions for offline development."""
from __future__ import annotations

from agentclaw.community.log import get_logger
from agentclaw.community.plugins.local._mock_seam import MockSeam
from agentclaw.community.plugin_api.mcp_auth import MCPAuthPlugin
from typing import Any
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl

logger = get_logger()


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="no I/O",
)
class LocalMCPAuthPlugin(MockSeam, MCPAuthPlugin):
    """Mock MCP auth client for local/SQLite mode.

    All permission checks return authorized.
    """

    def check_permission(
        self,
        staff_no: str,
        service_code: str,
        tool_list: list[str] | None = None,
        is_public: bool = True,
    ) -> dict[str, bool]:
        logger.debug("[LocalMCPAuthClient] check_permission(%s, %s) -> mock True", staff_no, service_code)
        result: dict[str, bool] = {service_code: True}
        if tool_list:
            for tool in tool_list:
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
        logger.debug("[LocalMCPAuthClient] apply_permission(%s, %s) -> mock success", staff_no, service_code)
        return {
            "success": True,
            "process_url": None,
            "error": None,
        }

    def query_permission_status(
        self,
        staff_no: str,
        service_code: str,
        tool_list: list[str] | None = None,
        is_public: bool = True,
    ) -> list[dict[str, Any]]:
        logger.debug("[LocalMCPAuthClient] query_permission_status(%s, %s) -> mock AUTHORIZED", staff_no, service_code)
        results: list[dict[str, Any]] = [
            {
                "resource_code": service_code,
                "auth_state": "AUTHORIZED",
                "auth_state_name": "已授权",
                "permission_code": "",
                "resource_name": "",
                "process_url": None,
                "create_date": None,
            },
        ]
        if tool_list:
            for tool in tool_list:
                results.append(
                    {
                        "resource_code": f"{service_code}/{tool}",
                        "auth_state": "AUTHORIZED",
                        "auth_state_name": "已授权",
                        "permission_code": "",
                        "resource_name": "",
                        "process_url": None,
                        "create_date": None,
                    }
                )
        return results

    def exchange_iam_token(self, subject_token: str) -> str | None:
        """Local mode: no token exchange available."""
        return None
