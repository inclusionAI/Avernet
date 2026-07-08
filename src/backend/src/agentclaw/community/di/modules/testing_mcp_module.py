"""TestingMcpModule — SQLite + local-mode overrides for the mcp module.

Overrides the mode-keyed bindings from :class:`McpModule`:

- ``MCPAuthPlugin`` → ``LocalMCPAuthPlugin`` (no-op stub)

``UserMCPConfigRepository`` is no longer overridden — it is now a single
unified ORM repository (bound in :class:`McpModule`) that runs on SQLite
too, differing only by the injected ``DatabasePlugin``.

The four services and the cross-Protocol binding (``BotMCPProvider``)
is mode-agnostic — it picks up the swapped plugins through the injector
without needing overrides.
"""
from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.mcp_auth import MCPAuthPlugin
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin


logger = get_logger()


class TestingMcpModule(Module):
    """Local + SQLite overrides for mcp."""

    # UserMCPConfigRepository is no longer overridden: the unified
    # repository (bound in McpModule) runs on SQLite too — it only needs
    # the SQLite DatabasePlugin injected. One impl, both runtimes.

    @singleton
    @provider
    def mcp_auth(self) -> MCPAuthPlugin:
        from agentclaw.community.plugins.local.mcp_auth import LocalMCPAuthPlugin

        logger.info(
            "[NEW-ARCH] MCPAuthPlugin: LocalMCPAuthPlugin (testing override)"
        )
        return LocalMCPAuthPlugin()

    @singleton
    @provider
    def mcp_center(self) -> MCPCenterPlugin:
        """Local Noop (MockSeam) instead of the prod MCP-Center HTTP impl.
        The catalog is a third-party HTTP API with no fake in prod; the
        local Noop now carries the mock seam, so tests can drive
        ``get_mcp_detail`` etc. without network access."""
        from agentclaw.community.plugins.local.mcp_center import NoopMCPCenterPlugin

        logger.info(
            "[NEW-ARCH] MCPCenterPlugin: NoopMCPCenterPlugin (testing override)"
        )
        return NoopMCPCenterPlugin()
