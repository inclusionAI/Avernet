"""MCP-center concern — community binding.

Capability: read-only MCP catalog + permission. B7 binds a real local registry
(``CommunityMCPCenter``): config-file catalog (empty by default), allow-all
permission, default tenant. The ``CommunityMcpConfig`` provider lives here
(community-only) and reads the ``mcp`` block of ``user_config``.
"""
from __future__ import annotations

from injector import Module, inject, provider, singleton

from agentclaw.community.di import config_community as cfg
from agentclaw.community.plugin_api.mcp_auth import MCPAuthPlugin
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin


class CommunityMcpCenterModule(Module):
    """community: local MCP registry (config-file catalog, allow-all) + permissive auth."""

    @singleton
    @provider
    def mcp_config(self) -> cfg.CommunityMcpConfig:
        """Read the ``mcp`` block; fall back to dataclass defaults."""
        from agentclaw.community.di.modules.config_module import _block

        block = _block("mcp")
        defaults = cfg.CommunityMcpConfig()
        return cfg.CommunityMcpConfig(
            registry_config_path=block.get(
                "registry_config_path", defaults.registry_config_path
            ),
        )

    @singleton
    @provider
    @inject
    def mcp_center(self, config: cfg.CommunityMcpConfig) -> MCPCenterPlugin:
        from agentclaw.community.plugins.community.mcp_center import CommunityMCPCenter

        return CommunityMCPCenter(
            registry_config_path=config.registry_config_path or None
        )

    @singleton
    @provider
    def mcp_auth(self) -> MCPAuthPlugin:
        from agentclaw.community.plugins.community.mcp_auth import CommunityMCPAuthPlugin

        return CommunityMCPAuthPlugin()
