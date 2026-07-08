"""Contract tests for OpenClawEngine.

Verifies that:
  - OpenClawEngine satisfies the Engine Protocol structurally
    (`isinstance(engine, Engine)` works via @runtime_checkable)
  - Declared capabilities match the plugins actually assigned
  - Session / chat / cron plugins are wired up and share one client
  - BaseEngine's validate_capabilities() passes (no declared-vs-assigned
    mismatches)

Tests do NOT exercise gateway RPC calls — those require a live OpenClaw
Gateway. Plugin-method behaviour is covered separately in
`tests/test_session.py`, `tests/test_chat.py`, `tests/test_cron.py` with
mocked gateway clients.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from engine.community.core.adapters.openclaw.chat import OpenClawChatAdapter
from engine.community.core.adapters.openclaw.cron import OpenClawCronAdapter
from engine.community.core.adapters.openclaw.models import OpenClawModelsAdapter
from engine.community.core.adapters.openclaw.session import OpenClawSessionAdapter
from engine.community.core.engine.capability import Capability
from engine.community.core.engine.protocol import Engine
from engine.community.engines.openclaw.engine import OpenClawEngine


def _fake_client() -> MagicMock:
    """A fake OpenClawGatewayClient. Tests that need real behaviour replace the
    plugin instances directly."""
    client = MagicMock()
    client.connected = False
    return client


class TestOpenClawEngineMetadata:
    def test_name_is_openclaw(self):
        assert OpenClawEngine.name == "openclaw"

    def test_version_is_string(self):
        assert isinstance(OpenClawEngine.version, str)
        assert OpenClawEngine.version

    def test_capabilities_declared_on_class_level(self):
        # Property accessor on instance; class-level _CAPABILITIES is an impl detail
        engine = OpenClawEngine(client=_fake_client())
        caps = engine.capabilities
        assert caps.supports(Capability.SESSION_LIST)
        assert caps.supports(Capability.CHAT_STREAM)
        assert caps.supports(Capability.CRON_LIST)

    def test_model_capabilities_declared(self):
        # Pairs with test_models_plugin_is_wired — if one side drifts,
        # validate_capabilities() raises. Both halves land together or
        # not at all.
        caps = OpenClawEngine(client=_fake_client()).capabilities
        assert caps.supports(Capability.MODEL_LIST)
        assert caps.supports(Capability.MODEL_SWITCH)


class TestOpenClawEngineWiring:
    def test_constructs_adapters(self):
        # Post-F2: the engine is assembled from the core/adapters/openclaw ACL,
        # not the legacy in-tree services.
        engine = OpenClawEngine(client=_fake_client())
        assert isinstance(engine.session, OpenClawSessionAdapter)
        assert isinstance(engine.chat, OpenClawChatAdapter)
        assert isinstance(engine.cron, OpenClawCronAdapter)

    def test_models_adapter_is_wired(self):
        # Regression: MODEL_LIST / MODEL_SWITCH were added to the capability
        # matrix but the plugin was only assigned on the aicoding side. Pin the
        # wiring so the next capability domain fails a unit test, not a boot.
        engine = OpenClawEngine(client=_fake_client())
        assert isinstance(engine.models, OpenClawModelsAdapter)

    def test_adapters_share_one_plugin_impl(self):
        """All adapters delegate to a single OpenClawPluginImpl — one shared
        gateway client + token pool (replaces the legacy shared-client wiring)."""
        engine = OpenClawEngine(client=_fake_client())
        assert engine._port is not None
        assert engine.session._port is engine._port
        assert engine.chat._port is engine._port
        assert engine.cron._port is engine._port

    def test_default_construction_builds_a_port(self):
        """Default construction (no injected client/pool) still assembles a port
        impl so EngineManager's connect/close lifecycle keeps working."""
        engine = OpenClawEngine()
        assert engine._port is not None
        assert engine.session._port is engine._port

    def test_all_service_slots_are_the_right_adapter(self):
        """Every Engine-protocol service slot is wired to its ACL adapter (bash
        reuses the core default). Covers the slot exposure that
        test_plugin_surface used to assert per-service."""
        from engine.community.core.adapters.openclaw.approval import OpenClawApprovalAdapter
        from engine.community.core.adapters.openclaw.default_config import (
            OpenClawDefaultConfigAdapter,
        )
        from engine.community.core.adapters.openclaw.file import OpenClawFileAdapter
        from engine.community.core.adapters.openclaw.mcp import OpenClawMcpAdapter
        from engine.community.core.adapters.openclaw.node import OpenClawNodeAdapter
        from engine.community.core.adapters.openclaw.relay import OpenClawRelayAdapter
        from engine.community.core.adapters.openclaw.skills import OpenClawSkillsAdapter
        from engine.community.core.adapters.openclaw.web_shell import OpenClawWebShellAdapter
        from engine.community.core.bash.base import BaseBashService

        engine = OpenClawEngine(client=_fake_client())
        assert isinstance(engine.relay, OpenClawRelayAdapter)
        assert isinstance(engine.approval, OpenClawApprovalAdapter)
        assert isinstance(engine.mcp, OpenClawMcpAdapter)
        assert isinstance(engine.skills, OpenClawSkillsAdapter)
        assert isinstance(engine.file, OpenClawFileAdapter)
        assert isinstance(engine.node, OpenClawNodeAdapter)
        assert isinstance(engine.default_config, OpenClawDefaultConfigAdapter)
        assert isinstance(engine.web_shell, OpenClawWebShellAdapter)
        assert isinstance(engine.bash, BaseBashService)


class TestOpenClawEngineProtocolConformance:
    def test_is_instance_of_engine_protocol(self):
        """@runtime_checkable isinstance check verifies structural conformance —
        OpenClawEngine does NOT inherit from Engine, only from BaseEngine."""
        engine = OpenClawEngine(client=_fake_client())
        assert isinstance(engine, Engine)

    def test_validate_capabilities_passes(self):
        """All declared capabilities have a matching plugin assigned; all
        assigned plugins have at least one declared capability."""
        engine = OpenClawEngine(client=_fake_client())
        # Raises EngineError on any mismatch
        engine.validate_capabilities()


class TestOpenClawEngineLifecycle:
    @pytest.mark.asyncio
    async def test_initialize_is_awaitable_and_returns_none(self):
        """With an injected client, initialize() short-circuits (returns None
        without starting the monitor or connecting) — the test-injection seam.
        The production-path monitor lifecycle is covered by the parity gate."""
        engine = OpenClawEngine(client=_fake_client())
        assert await engine.initialize() is None

    @pytest.mark.asyncio
    async def test_shutdown_is_awaitable_and_returns_none(self):
        engine = OpenClawEngine(client=_fake_client())
        assert await engine.shutdown() is None

    @pytest.mark.asyncio
    async def test_health_check_returns_healthy_by_default(self):
        engine = OpenClawEngine(client=_fake_client())
        status = await engine.health_check()
        assert status.healthy is True


class TestOpenClawEngineConnectionHooks:
    """Phase C — on_connection_open / on_connection_close forward to the pool.

    The generic WS server (`api/transport/ws_server.py`) calls these on handshake /
    disconnect so per-tenant routing stays inside the engine. OpenClaw
    forwards to its TokenClientPool's refcount.
    """

    @pytest.mark.asyncio
    async def test_open_registers_on_pool(self):
        from unittest.mock import AsyncMock
        from engine.community.core.engine.context import AuthContext
        from engine.community.plugins.openclaw.token_pool import TokenClientPool

        pool = TokenClientPool()
        engine = OpenClawEngine(client=_fake_client(), pool=pool)
        auth = AuthContext(token="tok-a")

        # Start state: no refcount entry.
        assert pool._refcount == {}

        await engine.on_connection_open(auth)
        assert pool._refcount == {"tok-a": 1}

        # Second open on same token bumps the refcount.
        await engine.on_connection_open(auth)
        assert pool._refcount == {"tok-a": 2}

    @pytest.mark.asyncio
    async def test_close_releases_from_pool(self):
        from engine.community.core.engine.context import AuthContext
        from engine.community.plugins.openclaw.token_pool import TokenClientPool

        pool = TokenClientPool()
        engine = OpenClawEngine(client=_fake_client(), pool=pool)
        auth = AuthContext(token="tok-a")

        # register twice so we can observe the decrement (token-keyed pool).
        pool.register(auth.token)
        pool.register(auth.token)

        await engine.on_connection_close(auth)
        assert pool._refcount == {"tok-a": 1}

        await engine.on_connection_close(auth)
        # Hit zero — refcount entry cleared.
        assert "tok-a" not in pool._refcount

    @pytest.mark.asyncio
    async def test_open_with_none_auth_is_noop(self):
        from engine.community.plugins.openclaw.token_pool import TokenClientPool

        pool = TokenClientPool()
        engine = OpenClawEngine(client=_fake_client(), pool=pool)

        await engine.on_connection_open(None)
        await engine.on_connection_close(None)
        # No token → nothing tracked, no error.
        assert pool._refcount == {}


class TestOpenClawEngineSystemEventMonitor:
    """Phase D — `SystemEventMonitorService` startup lifted from
    `EngineManager._start_cron_services` into `OpenClawEngine.initialize()`.

    Test-injected client paths (`OpenClawEngine(client=...)`) skip the
    monitor on purpose so unit tests don't spawn a background asyncio task
    on the test loop. The non-test path is exercised here by patching
    `get_client` and the SystemEventMonitorService class directly so we can
    observe the start/stop calls without a real cron polling cycle.
    """

    @pytest.mark.asyncio
    async def test_initialize_starts_monitor(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        engine = OpenClawEngine()  # production path: no injected client

        # Stub the gateway-client connect so the test doesn't open a real WS.
        monkeypatch.setattr(
            "engine.community.engines.openclaw.engine.get_client",
            AsyncMock(return_value=MagicMock()),
        )
        monkeypatch.setattr(
            "engine.community.engines.openclaw.engine.close_client",
            AsyncMock(),
        )
        # Stub the pool's shutdown for symmetry on engine.shutdown() teardown.
        engine.token_pool.shutdown = AsyncMock()  # type: ignore[method-assign]

        # Patch the monitor class — start() and stop() become AsyncMocks.
        monitor_instance = MagicMock(name="SystemEventMonitorService")
        monitor_instance.start = AsyncMock()
        monitor_instance.stop = AsyncMock()
        monitor_cls = MagicMock(return_value=monitor_instance)
        monkeypatch.setattr(
            "engine.community.core.cron.services.systemevent_monitor.SystemEventMonitorService",
            monitor_cls,
        )

        try:
            await engine.initialize()

            # Constructed with the engine's own cron plugin — engine-agnostic
            # at the manager level, OpenClaw owns the wiring here.
            monitor_cls.assert_called_once()
            kwargs = monitor_cls.call_args.kwargs
            assert kwargs["engine"] == "openclaw"
            assert kwargs["cron_api"] is engine._cron
            monitor_instance.start.assert_awaited_once()
            assert engine._systemevent_monitor is monitor_instance
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_stops_monitor(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        engine = OpenClawEngine()

        monkeypatch.setattr(
            "engine.community.engines.openclaw.engine.get_client",
            AsyncMock(return_value=MagicMock()),
        )
        monkeypatch.setattr(
            "engine.community.engines.openclaw.engine.close_client",
            AsyncMock(),
        )
        engine.token_pool.shutdown = AsyncMock()  # type: ignore[method-assign]

        monitor_instance = MagicMock()
        monitor_instance.start = AsyncMock()
        monitor_instance.stop = AsyncMock()
        monkeypatch.setattr(
            "engine.community.core.cron.services.systemevent_monitor.SystemEventMonitorService",
            MagicMock(return_value=monitor_instance),
        )

        await engine.initialize()
        await engine.shutdown()

        monitor_instance.stop.assert_awaited_once()
        assert engine._systemevent_monitor is None

    @pytest.mark.asyncio
    async def test_initialize_swallows_monitor_failure(self, monkeypatch):
        # Monitor failure must not block engine startup — cron still works
        # via the polling service; only the auto-replace feature is degraded.
        from unittest.mock import AsyncMock, MagicMock

        engine = OpenClawEngine()
        monkeypatch.setattr(
            "engine.community.engines.openclaw.engine.get_client",
            AsyncMock(return_value=MagicMock()),
        )
        monkeypatch.setattr(
            "engine.community.engines.openclaw.engine.close_client",
            AsyncMock(),
        )
        engine.token_pool.shutdown = AsyncMock()  # type: ignore[method-assign]

        monkeypatch.setattr(
            "engine.community.core.cron.services.systemevent_monitor.SystemEventMonitorService",
            MagicMock(side_effect=RuntimeError("boom")),
        )

        try:
            # Should not raise.
            await engine.initialize()
            assert engine._systemevent_monitor is None
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_injected_client_skips_monitor_startup(self):
        # Test path — injected client → engine.initialize() returns early
        # without touching the monitor (avoids dirtying the test event loop
        # with a long-running asyncio task).
        engine = OpenClawEngine(client=_fake_client())
        await engine.initialize()
        assert engine._systemevent_monitor is None
