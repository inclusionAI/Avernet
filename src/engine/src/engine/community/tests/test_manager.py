"""Unit tests for EngineManager.

Covers the M2 rewrite — registry-based engine activation, `.session` /
`.chat` / `.cron` passthrough properties, and the switch/restart/shutdown
lifecycle. Heavyweight collaborators (engine process, cron polling, system
event monitor, WS server singleton, LAST_ENGINE_FILE) are patched so tests
don't need a live gateway.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.exceptions import (
    CapabilityNotSupportedError,
    EngineError,
)
from engine.community.core.engine.registry import EngineRegistry
from engine.community.manager import EngineManager

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


class _FakeEngine(BaseEngine):
    """Minimal BaseEngine subclass wired with MagicMock plugins.

    Tracks `initialize()` / `shutdown()` call counts so lifecycle tests can
    verify the manager invoked them.
    """

    name = "fake"
    version = "0.0.1"

    _CAPS = EngineCapabilities(
        supported={
            Capability.SESSION_LIST,
            Capability.CHAT_STREAM,
            Capability.CRON_LIST,
        }
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPS

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock(name="FakeSession")
        self._chat = MagicMock(name="FakeChat")
        self._cron = MagicMock(name="FakeCron")
        # Lifecycle bookkeeping
        self.initialized = 0
        self.shutdown_called = 0

    async def initialize(self) -> None:
        self.initialized += 1

    async def shutdown(self) -> None:
        self.shutdown_called += 1


class _CronlessEngine(BaseEngine):
    """Engine that declares only session+chat — `cron` returns None."""

    name = "cronless"
    version = "0.0.1"

    _CAPS = EngineCapabilities(
        supported={Capability.SESSION_LIST, Capability.CHAT_STREAM}
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPS

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock()
        self._chat = MagicMock()
        # _cron intentionally left None


@pytest.fixture
def registry() -> EngineRegistry:
    r = EngineRegistry()
    r.register(_FakeEngine)
    return r


@pytest.fixture
def manager(registry: EngineRegistry) -> EngineManager:
    """A fresh EngineManager bound to an isolated registry (no singleton leak)."""
    EngineManager.reset_instance()
    return EngineManager("fake", registry=registry)


@pytest.fixture
def patch_manager_deps(tmp_path):
    """Patch heavy lifecycle collaborators so initialize/switch/restart run quickly.

    Returns a dict exposing the mocks so tests can assert interactions.
    """
    process = MagicMock(name="EngineProcess")
    process.start = AsyncMock()
    process.stop = AsyncMock()
    process.restart = AsyncMock()
    process.is_running = AsyncMock(return_value=True)
    process.status = MagicMock(return_value={"running": True})

    create_process = MagicMock(return_value=process)

    polling = MagicMock(name="CronPollingService")
    polling.start = AsyncMock()
    polling.stop = AsyncMock()
    polling_cls = MagicMock(return_value=polling)

    monitor = MagicMock(name="SystemEventMonitorService")
    monitor.start = AsyncMock()
    monitor.stop = AsyncMock()
    monitor_cls = MagicMock(return_value=monitor)

    ws_server = MagicMock()
    ws_server._connections = {}

    last_engine_file = tmp_path / "last_engine"

    patches = [
        patch("engine.community.process.create_engine_process", create_process),
        patch(
            "engine.community.core.cron.services.polling.CronPollingService",
            polling_cls,
        ),
        patch(
            "engine.community.core.cron.services.systemevent_monitor.SystemEventMonitorService",
            monitor_cls,
        ),
        patch(
            "engine.community.api.transport.ws_server.get_server",
            return_value=ws_server,
        ),
        patch(
            "engine.community.api.transport.ws_server.reset_server",
            MagicMock(),
        ),
        patch("engine.community.core.cron.constants.LAST_ENGINE_FILE", last_engine_file),
    ]
    for p in patches:
        p.start()

    yield {
        "process": process,
        "create_process": create_process,
        "polling": polling,
        "polling_cls": polling_cls,
        "monitor": monitor,
        "monitor_cls": monitor_cls,
        "ws_server": ws_server,
        "last_engine_file": last_engine_file,
    }

    for p in patches:
        p.stop()


# ─────────────────────────────────────────────────────────────────────────────
# Passthrough properties
# ─────────────────────────────────────────────────────────────────────────────


class TestPassthroughProperties:
    def test_require_engine_raises_before_initialize(self, manager: EngineManager):
        with pytest.raises(RuntimeError, match="call await initialize"):
            _ = manager.session

    def test_session_delegates_to_active_engine(self, manager: EngineManager):
        fake = _FakeEngine()
        manager._active_engine = fake
        assert manager.session is fake._session

    def test_chat_delegates_to_active_engine(self, manager: EngineManager):
        fake = _FakeEngine()
        manager._active_engine = fake
        assert manager.chat is fake._chat

    def test_cron_delegates_to_active_engine(self, manager: EngineManager):
        fake = _FakeEngine()
        manager._active_engine = fake
        assert manager.cron is fake._cron

    def test_cron_raises_when_engine_returns_none(self, manager: EngineManager):
        manager._active_engine = _CronlessEngine()
        with pytest.raises(CapabilityNotSupportedError):
            _ = manager.cron

    def test_engine_property_reflects_active_name(self, manager: EngineManager):
        assert manager.engine == "fake"

    def test_approval_delegates_to_active_engine(self, manager: EngineManager):
        # Phase D — `manager.approval` is the new passthrough that replaces
        # `manager.get_client_getter()` as the entry into the engine's
        # session-level approval API.
        fake = _FakeEngine()
        approval_plugin = MagicMock(name="FakeApproval")
        fake._approval = approval_plugin
        manager._active_engine = fake
        assert manager.approval is approval_plugin

    def test_approval_raises_when_engine_returns_none(self, manager: EngineManager):
        # Engines that don't expose approval (e.g. AiCoding's gateway has no
        # `exec.approvals.*`) cause `manager.approval` to raise the same
        # CapabilityNotSupportedError that other optional plugins raise.
        manager._active_engine = _CronlessEngine()  # also leaves _approval as None
        with pytest.raises(CapabilityNotSupportedError):
            _ = manager.approval


# ─────────────────────────────────────────────────────────────────────────────
# initialize() / _activate_engine
# ─────────────────────────────────────────────────────────────────────────────


class TestInitialize:
    @pytest.mark.asyncio
    async def test_activates_engine_from_registry(
        self, manager: EngineManager, patch_manager_deps
    ):
        await manager.initialize()
        assert isinstance(manager._active_engine, _FakeEngine)
        assert manager._active_engine.initialized == 1

    @pytest.mark.asyncio
    async def test_starts_engine_process(
        self, manager: EngineManager, patch_manager_deps
    ):
        await manager.initialize()
        patch_manager_deps["create_process"].assert_called_once_with("fake")
        patch_manager_deps["process"].start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_writes_last_engine_marker(
        self, manager: EngineManager, patch_manager_deps
    ):
        await manager.initialize()
        marker = patch_manager_deps["last_engine_file"]
        assert marker.exists()
        assert marker.read_text() == "fake"

    @pytest.mark.asyncio
    async def test_validates_capabilities(
        self,
        registry: EngineRegistry,
        patch_manager_deps,
    ):
        # Swap the registered engine for one with a declared-vs-assigned mismatch.
        class _BadEngine(BaseEngine):
            name = "bad"
            version = "0.0.1"

            @property
            def capabilities(self) -> EngineCapabilities:
                # Declares SESSION but never assigns self._session
                return EngineCapabilities(supported={Capability.SESSION_LIST})

            def __init__(self, config: dict | None = None) -> None:
                super().__init__(config)
                # Both mandatory plugins left None — validate_capabilities should flag it

        registry.register(_BadEngine)
        EngineManager.reset_instance()
        mgr = EngineManager("bad", registry=registry)
        with pytest.raises(EngineError, match="inconsistent capability"):
            await mgr.initialize()

    @pytest.mark.asyncio
    async def test_starts_cron_services(
        self, manager: EngineManager, patch_manager_deps
    ):
        await manager.initialize()
        patch_manager_deps["polling"].start.assert_awaited_once()
        # Phase D — manager no longer owns SystemEventMonitor. That worker
        # lives inside `OpenClawEngine.initialize()` now; non-OpenClaw
        # engines never see it. The patched monitor class is a leftover
        # safety net — assert it's never touched from the manager path.
        patch_manager_deps["monitor"].start.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# switch()
# ─────────────────────────────────────────────────────────────────────────────


class TestSwitch:
    @pytest.mark.asyncio
    async def test_unregistered_target_raises(
        self, manager: EngineManager, patch_manager_deps
    ):
        with pytest.raises(ValueError, match="Unsupported engine type"):
            await manager.switch("nonexistent")

    @pytest.mark.asyncio
    async def test_same_engine_is_noop(
        self, manager: EngineManager, patch_manager_deps
    ):
        result = await manager.switch("fake")
        assert result == {
            "switched": False,
            "engine": "fake",
            "reason": "already active",
        }

    @pytest.mark.asyncio
    async def test_happy_path_deactivates_then_activates(
        self,
        registry: EngineRegistry,
        patch_manager_deps,
    ):
        # Register a second engine alongside fake.
        class _Fake2(_FakeEngine):
            name = "fake2"

        registry.register(_Fake2)
        EngineManager.reset_instance()
        mgr = EngineManager("fake", registry=registry)

        await mgr.initialize()
        first = mgr._active_engine
        assert isinstance(first, _FakeEngine)

        result = await mgr.switch("fake2")

        assert result["switched"] is True
        assert result["engine"] == "fake2"
        assert result["previous"] == "fake"
        # Old engine was shut down; new engine was initialized.
        assert first.shutdown_called == 1
        assert isinstance(mgr._active_engine, _Fake2)
        assert mgr._active_engine.initialized == 1
        assert mgr.engine == "fake2"


# ─────────────────────────────────────────────────────────────────────────────
# restart()
# ─────────────────────────────────────────────────────────────────────────────


class TestRestart:
    @pytest.mark.asyncio
    async def test_deactivates_then_reactivates_same_engine(
        self, manager: EngineManager, patch_manager_deps
    ):
        await manager.initialize()
        first = manager._active_engine
        assert first.initialized == 1

        result = await manager.restart()

        assert result == {"restarted": True, "engine": "fake"}
        assert first.shutdown_called == 1
        # Fresh instance — activation re-constructs from the registry.
        assert manager._active_engine is not first
        assert isinstance(manager._active_engine, _FakeEngine)
        assert manager._active_engine.initialized == 1
        patch_manager_deps["process"].restart.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────────────
# shutdown()
# ─────────────────────────────────────────────────────────────────────────────


class TestShutdown:
    @pytest.mark.asyncio
    async def test_stops_cron_and_engine_and_process(
        self, manager: EngineManager, patch_manager_deps
    ):
        await manager.initialize()
        active = manager._active_engine

        await manager.shutdown()

        assert active.shutdown_called == 1
        patch_manager_deps["polling"].stop.assert_awaited_once()
        patch_manager_deps["process"].stop.assert_awaited_once()
        assert manager._active_engine is None

    @pytest.mark.asyncio
    async def test_is_safe_when_not_initialized(
        self, manager: EngineManager, patch_manager_deps
    ):
        # Should not raise even though _active_engine is None.
        await manager.shutdown()


# ─────────────────────────────────────────────────────────────────────────────
# Registry injection (multi-engine readiness)
# ─────────────────────────────────────────────────────────────────────────────


class TestRegistryInjection:
    def test_custom_registry_used_over_default(self, registry: EngineRegistry):
        EngineManager.reset_instance()
        mgr = EngineManager("fake", registry=registry)
        assert mgr._registry is registry

    def test_default_registry_used_when_none_passed(self):
        EngineManager.reset_instance()
        from engine.community.core.engine.registry import DEFAULT_REGISTRY
        mgr = EngineManager("openclaw")
        assert mgr._registry is DEFAULT_REGISTRY
