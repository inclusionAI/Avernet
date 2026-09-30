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
        """Fixture-backed catalog when the singlebox acceptance fixture file is
        provided; otherwise the local Noop.

        ``SINGLEBOX_ACCEPTANCE_MCP_FIXTURE_FILE`` (set by the acceptance
        conftest) points at a LocalMCPRegistry JSON/YAML catalog. When present
        we serve a real ``CommunityMCPCenter`` from it so MCP-detail consumers
        — notably the D-TOOLS-002 diagnostic, whose prompt-slimming helpers
        only run when a tool exposes ``inputSchema`` — execute end-to-end under
        singlebox acceptance instead of falling back to ``tools: []``. Absent
        the env, fall back to the Noop (MockSeam) so other local tests degrade
        gracefully without network access."""
        import os
        from pathlib import Path

        fixture = os.environ.get("SINGLEBOX_ACCEPTANCE_MCP_FIXTURE_FILE", "").strip()
        if fixture and Path(fixture).is_file():
            from agentclaw.community.plugins.community.mcp_center import (
                CommunityMCPCenter,
            )

            logger.info(
                "[NEW-ARCH] MCPCenterPlugin: CommunityMCPCenter "
                "(fixture-backed, path=%s)",
                fixture,
            )
            return CommunityMCPCenter(registry_config_path=fixture)

        from agentclaw.community.plugins.local.mcp_center import NoopMCPCenterPlugin

        logger.info(
            "[NEW-ARCH] MCPCenterPlugin: NoopMCPCenterPlugin (testing override)"
        )
        return NoopMCPCenterPlugin()
