"""Rule 25 conformance — MCPCenterPlugin.

Consumer under test: ``MCPAuthService.check_mcp_permission_detail``
(core/mcp/services/auth_service.py:27). It calls
``mcp_center.get_mcp_detail(server_code)`` first; when the plugin
returns ``None`` the consumer short-circuits to a precise envelope
``{"has_permission": False, "access_level": "", "tool_permissions": {}}``.

Plugin-hit assertion: the local ``NoopMCPCenterPlugin`` returns
``None`` for ``get_mcp_detail``. The exact envelope above is the only
shape the consumer produces on that branch, so observing it proves
the consumer reached the plugin.
"""
from __future__ import annotations

from agentclaw.community.core.mcp.services.auth_service import MCPAuthService
from agentclaw.community.plugins.local.mcp_center import NoopMCPCenterPlugin


def test_check_mcp_permission_short_circuits_when_plugin_returns_none(world) -> None:
    svc = world.get(MCPAuthService)
    result = svc.check_mcp_permission_detail(
        user_id="alice", server_code="some_mcp"
    )
    assert result == {
        "has_permission": False,
        "access_level": "",
        "tool_permissions": {},
    }


def test_community_mcp_center_wired_into_consumer(community_world) -> None:
    """The community column wires CommunityMCPCenter (empty catalog by default):
    an unknown server_code yields ``get_mcp_detail`` -> None, so the consumer
    short-circuits to the precise no-permission envelope — proving the community
    plugin is reached end-to-end."""
    svc = community_world.get(MCPAuthService)
    result = svc.check_mcp_permission_detail(
        user_id="alice", server_code="some_mcp"
    )
    assert result == {
        "has_permission": False,
        "access_level": "",
        "tool_permissions": {},
    }


def test_local_mcp_center_detail_can_be_driven_by_mock_seam() -> None:
    plugin = NoopMCPCenterPlugin()
    server_code = "mcp.singlebox.acceptance.sync"

    assert plugin.get_mcp_detail(server_code) is None

    plugin.set_response(
        "get_mcp_detail",
        {
            "serverCode": server_code,
            "endpoints": [
                {"env": "PRE", "transportProtocol": "STREAMABLE_HTTP"}
            ],
        },
    )
    detail = plugin.get_mcp_detail(server_code)

    assert detail is not None
    assert detail["serverCode"] == server_code
    assert detail["endpoints"][0]["env"] == "PRE"
    assert detail["endpoints"][0]["transportProtocol"] == "STREAMABLE_HTTP"
