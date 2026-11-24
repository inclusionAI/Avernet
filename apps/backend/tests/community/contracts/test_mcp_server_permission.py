"""Optional bounded Server permission capability remains backward compatible."""
from agentclaw.community.plugin_api.mcp_auth import MCPServerPermissionPlugin
from agentclaw.community.plugins.local.mcp_auth import LocalMCPAuthPlugin


def test_legacy_plugin_without_bounded_capability_remains_valid():
    assert not isinstance(LocalMCPAuthPlugin(), MCPServerPermissionPlugin)


def test_structural_plugin_exposes_three_state_contract():
    class Plugin:
        def check_server_permission(self, staff_no: str, service_code: str, timeout_seconds: float) -> bool | None:
            return {"allowed": True, "denied": False}.get(service_code)

    plugin = Plugin()
    assert isinstance(plugin, MCPServerPermissionPlugin)
    assert [plugin.check_server_permission("user", code, 0.5) for code in ("allowed", "denied", "unknown")] == [True, False, None]
