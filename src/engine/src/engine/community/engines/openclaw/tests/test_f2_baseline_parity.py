"""F2 baseline parity — the cutover gate for the OpenClaw ACL conversion.

This test pins OpenClaw's *behavioral* invariants against a frozen golden
snapshot taken BEFORE the F2 ACL conversion. It is deliberately decoupled from
the concrete service classes (`OpenClawSessionService`, …): it asserts only what
must stay true after `OpenClawEngine` is reassembled from `core/adapters/openclaw`
adapters in Group E. The impl-identity assertions in `test_engine.py`
(`isinstance(engine.session, OpenClawSessionService)`) are expected to migrate at
cutover; the assertions here must NOT.

Captured invariants (reviewer S2):
  (a) `_CAPABILITIES` byte-identical (full supported + limited golden snapshot)
  (b) `token_pool` property still exposed (the OpenClaw WS server consumes it)
  (c) `on_connection_open` / `on_connection_close` forward to the pool refcount
  (d) `initialize()` / `shutdown()` ordering: monitor stop → pool shutdown → close
  (e) the SystemEvent monitor worker still starts/stops
  (f) the `_injected_client` / `_injected_pool` test-injection seams still work
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.protocol import Engine
from engine.community.engines.openclaw.engine import OpenClawEngine

# ── Golden snapshot of OpenClaw's declared capabilities (pre-F2). ──
# An INDEPENDENT copy — asserting against this (not against the engine's own
# `_CAPABILITIES`) is what makes drift during the conversion fail the gate.
GOLDEN_SUPPORTED = frozenset({
    # Session
    Capability.SESSION_LIST,
    Capability.SESSION_CREATE,
    Capability.SESSION_DELETE,
    Capability.SESSION_UPDATE,
    Capability.SESSION_HISTORY,
    # Chat
    Capability.CHAT_STREAM,
    Capability.CHAT_COMPLETE,
    Capability.CHAT_ABORT,
    Capability.CHAT_APPROVAL,
    Capability.CHAT_HISTORY,
    # MCP (full)
    Capability.MCP_LIST,
    Capability.MCP_CREATE,
    Capability.MCP_UPDATE,
    Capability.MCP_DELETE,
    Capability.MCP_TOOLS_LIST,
    Capability.MCP_TOOLS_CALL,
    Capability.MCP_RESOURCES_LIST,
    Capability.MCP_RESOURCES_READ,
    Capability.MCP_PROMPTS_LIST,
    Capability.MCP_PROMPTS_GET,
    Capability.MCP_FILTER_SERVERS,
    # Skills
    Capability.SKILLS_SYNC_SYMLINKS,
    Capability.SKILLS_SYNC_BINDPATHS,
    Capability.SKILLS_CLEAN_SYMLINKS,
    Capability.SKILLS_CENTER_ENSURE,
    # Approval
    Capability.APPROVAL_GET,
    Capability.APPROVAL_SET,
    # File
    Capability.FILE_READ,
    Capability.FILE_WRITE,
    Capability.FILE_UPLOAD,
    Capability.FILE_DELETE,
    Capability.FILE_LIST,
    # Bash
    Capability.BASH_EXEC,
    # Node
    Capability.NODE_LIST,
    Capability.NODE_REGISTER,
    Capability.NODE_STATUS,
    # Channel — NOTE: declared-but-unbacked in the pre-F2 engine (no
    # `_channel` service is wired in OpenClawEngine.__init__, no ChannelService
    # protocol exists, and `_PLUGIN_CAPABILITY_DOMAINS` has no channel entry so
    # `validate_capabilities()` can't catch the gap). This is a pre-existing
    # latent inconsistency, tracked separately — pinned here intentionally
    # because a characterization gate reproduces current reality, warts and all.
    Capability.CHANNEL_CONFIG_GET,
    Capability.CHANNEL_CONFIG_SET,
    Capability.CHANNEL_STATUS,
    # Cron
    Capability.CRON_LIST,
    Capability.CRON_CREATE,
    Capability.CRON_UPDATE,
    Capability.CRON_DELETE,
    Capability.CRON_RUN,
    Capability.CRON_HISTORY,
    # Model
    Capability.MODEL_LIST,
    Capability.MODEL_SWITCH,
    # Default config
    Capability.DEFAULT_CONFIG_GET,
    # Web shell
    Capability.WEB_SHELL_OPEN,
    # CLI tools (W9) — model-callable binaries placed by a config manifest.
    Capability.CLI_INSTALL,
    Capability.CLI_DELETE,
    Capability.CLI_LIST,
    Capability.CLI_REPLACE,
    Capability.CLI_DOWNLOAD,
})
GOLDEN_LIMITED = frozenset({Capability.MCP_START, Capability.MCP_STOP})


def _fake_client() -> MagicMock:
    client = MagicMock()
    client.connected = False
    return client


class TestCapabilityParity:
    def test_supported_capabilities_match_golden(self):
        caps = OpenClawEngine(client=_fake_client()).capabilities
        assert isinstance(caps, EngineCapabilities)
        assert set(caps.supported) == set(GOLDEN_SUPPORTED)

    def test_limited_capabilities_match_golden(self):
        caps = OpenClawEngine(client=_fake_client()).capabilities
        assert set(caps.limited.keys()) == set(GOLDEN_LIMITED)

    def test_capabilities_available_as_class_attribute(self):
        # The capabilities-inspection path (manager.py:210-219) reads
        # `cls._CAPABILITIES` WITHOUT instantiating — preserve that.
        assert isinstance(
            OpenClawEngine._CAPABILITIES, EngineCapabilities,
        )
        assert set(OpenClawEngine._CAPABILITIES.supported) == set(GOLDEN_SUPPORTED)

    def test_validate_capabilities_passes(self):
        OpenClawEngine(client=_fake_client()).validate_capabilities()

    def test_is_instance_of_engine_protocol(self):
        assert isinstance(OpenClawEngine(client=_fake_client()), Engine)


class TestTokenPoolExposure:
    def test_token_pool_property_exposes_the_pool(self):
        # The (OpenClaw-specific) WS server calls register/release on this.
        from engine.community.plugins.openclaw.token_pool import TokenClientPool

        pool = TokenClientPool()
        engine = OpenClawEngine(client=_fake_client(), pool=pool)
        assert engine.token_pool is pool


class TestConnectionHookForwarding:
    @pytest.mark.asyncio
    async def test_open_and_close_drive_pool_refcount(self):
        from engine.community.core.engine.context import AuthContext
        from engine.community.plugins.openclaw.token_pool import TokenClientPool

        pool = TokenClientPool()
        engine = OpenClawEngine(client=_fake_client(), pool=pool)
        auth = AuthContext(token="tok-z")

        await engine.on_connection_open(auth)
        assert pool._refcount == {"tok-z": 1}
        await engine.on_connection_close(auth)
        assert "tok-z" not in pool._refcount


class TestLifecycleOrdering:
    @pytest.mark.asyncio
    async def test_shutdown_order_monitor_then_pool_then_client(self, monkeypatch):
        """Order matters: monitor uses cron (→ gateway client), so it must be
        torn down before the pool, which must be torn down before the
        module-level client is disconnected."""
        order: list[str] = []

        engine = OpenClawEngine()  # production path (no injected client/pool)

        monkeypatch.setattr(
            "engine.community.engines.openclaw.engine.get_client",
            AsyncMock(return_value=MagicMock()),
        )

        async def _close():
            order.append("close_client")

        monkeypatch.setattr(
            "engine.community.engines.openclaw.engine.close_client",
            AsyncMock(side_effect=_close),
        )

        async def _pool_shutdown():
            order.append("pool.shutdown")

        engine.token_pool.shutdown = AsyncMock(side_effect=_pool_shutdown)  # type: ignore[method-assign]

        monitor = MagicMock()
        monitor.start = AsyncMock()

        async def _monitor_stop():
            order.append("monitor.stop")

        monitor.stop = AsyncMock(side_effect=_monitor_stop)
        monkeypatch.setattr(
            "engine.community.core.cron.services.systemevent_monitor.SystemEventMonitorService",
            MagicMock(return_value=monitor),
        )

        await engine.initialize()
        monitor.start.assert_awaited_once()

        await engine.shutdown()
        assert order == ["monitor.stop", "pool.shutdown", "close_client"]


class TestInjectionSeams:
    @pytest.mark.asyncio
    async def test_injected_client_skips_monitor_and_client_lifecycle(self):
        # Injected client → initialize() returns early (no monitor, no connect).
        engine = OpenClawEngine(client=_fake_client())
        await engine.initialize()
        assert engine._systemevent_monitor is None
        # shutdown with injected client must not touch the module singleton.
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_injected_pool_is_owned_by_caller(self):
        # Injected pool → engine.shutdown() must NOT shut it down.
        from engine.community.plugins.openclaw.token_pool import TokenClientPool

        pool = TokenClientPool()
        pool.shutdown = AsyncMock()  # type: ignore[method-assign]
        engine = OpenClawEngine(client=_fake_client(), pool=pool)
        await engine.shutdown()
        pool.shutdown.assert_not_awaited()


class TestCliToolsBinding:
    """W9. The service is bound, and it is *this* engine's directory."""

    def test_cli_tools_service_is_assigned(self):
        engine = OpenClawEngine(client=_fake_client())

        assert engine.cli_tools is not None

    def test_openclaw_and_claude_code_never_share_a_tool_directory(self):
        """A future engine must not silently inherit OpenClaw's tree.

        The two workspaces genuinely differ — OpenClaw's is env-injected,
        Claude Code's is ``<home>/.claude_code/workspace`` — so one shared
        resolver would put both engines' tools in one directory, where a
        whole-set replacement on either would delete the other's.
        """
        from engine.community.core.cli_tools.directories import (
            claude_code_cli_dir,
            openclaw_cli_dir,
        )

        assert openclaw_cli_dir() != claude_code_cli_dir()
