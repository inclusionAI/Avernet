"""Rule 25 conformance — MCPAuthPlugin.

Consumer under test: ``MCPAuthService.apply_permission``
(core/mcp/services/auth_service.py:87). It forwards to
``auth_client.apply_permission(...)`` and returns the plugin's result
verbatim. The local ``LocalMCPAuthPlugin`` returns a fixed
``{"success": True, ...}`` envelope.

Plugin-hit assertion: the consumer's return value must carry
``success=True`` — only producible by the local plugin's mock
permission grant.
"""
from __future__ import annotations

from agentclaw.community.core.mcp.services.auth_service import MCPAuthService


def test_apply_permission_routes_through_mcp_auth_client(world) -> None:
    svc = world.get(MCPAuthService)
    result = svc.apply_permission(
        staff_no="alice",
        service_code="demo_mcp",
        tool_list=["t1"],
    )
    # The local mock always returns success=True. A consumer bypassing
    # the plugin would not produce this envelope.
    assert result["success"] is True


def test_community_mcp_auth_grants_through_consumer(community_world) -> None:
    """The community column wires a permissive MCPAuthPlugin: apply_permission
    succeeds end-to-end (allow-all, no remote auth service)."""
    svc = community_world.get(MCPAuthService)
    result = svc.apply_permission(
        staff_no="alice",
        service_code="demo_mcp",
        tool_list=["t1"],
    )
    assert result["success"] is True
