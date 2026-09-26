"""Verify MCPCenterPlugin protocol is structurally sound."""
from typing import Protocol


def test_mcp_center_plugin_protocol_exists():
    from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
    assert hasattr(MCPCenterPlugin, "get_mcp_detail")
    assert hasattr(MCPCenterPlugin, "get_mcp_list")
    assert hasattr(MCPCenterPlugin, "check_mcp_permission_detail")


def test_mcp_center_plugin_is_protocol():
    from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
    assert issubclass(MCPCenterPlugin, Protocol)
