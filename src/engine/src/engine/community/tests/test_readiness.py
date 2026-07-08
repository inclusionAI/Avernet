"""Unit tests for EngineManager.readiness().

Covers the 4×2 lookup over (`_init_phase`, subprocess liveness) defined in
EngineManager._READINESS_TABLE, plus the lifecycle phase transitions
(`starting` → `start_attempted` → `completed`/`failed`) wired into
`initialize()`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.registry import EngineRegistry
from engine.community.manager import EngineManager


class _FakeEngine(BaseEngine):
    name = "fake"
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

    async def initialize(self) -> None: ...
    async def shutdown(self) -> None: ...


@pytest.fixture
def registry() -> EngineRegistry:
    r = EngineRegistry()
    r.register(_FakeEngine)
    return r


@pytest.fixture
def manager(registry: EngineRegistry) -> EngineManager:
    EngineManager.reset_instance()
    return EngineManager("fake", registry=registry)


@pytest.fixture
def patch_lifecycle_deps(tmp_path):
    """Patch heavy lifecycle collaborators so initialize runs quickly."""
    process = MagicMock(name="EngineProcess")
    process.start = AsyncMock()
    process.stop = AsyncMock()
    process.is_running = AsyncMock(return_value=True)
    process.status = MagicMock(return_value={"running": True})

    polling = MagicMock()
    polling.start = AsyncMock()
    polling.stop = AsyncMock()

    last_engine_file = tmp_path / "last_engine"

    patches = [
        patch(
            "engine.community.process.create_engine_process",
            MagicMock(return_value=process),
        ),
        patch(
            "engine.community.core.cron.services.polling.CronPollingService",
            MagicMock(return_value=polling),
        ),
        patch("engine.community.core.cron.constants.LAST_ENGINE_FILE", last_engine_file),
    ]
    for p in patches:
        p.start()

    yield {"process": process, "polling": polling}

    for p in patches:
        p.stop()


def _make_process(running: bool) -> MagicMock:
    """Build a process mock whose `is_running()` returns the given value."""
    proc = MagicMock(name="EngineProcess")
    proc.is_running = AsyncMock(return_value=running)
    return proc


class TestReadinessTable:
    """Drive the 4×2 lookup directly by setting `_init_phase` + a fake process.

    Covers every cell of EngineManager._READINESS_TABLE. The lifecycle wiring
    (which phase the manager is in when) is exercised separately below.
    """

    @pytest.mark.parametrize(
        "phase,running,expected_state,expected_message",
        [
            ("starting",        True,  "starting",            "引擎已启动，初始化中"),
            ("starting",        False, "starting",            "引擎启动中"),
            ("start_attempted", True,  "starting",            "引擎已启动，初始化中"),
            ("start_attempted", False, "starting",            "引擎启动中，耗时异常"),
            ("completed",       True,  "ready",               "启动完成，服务中"),
            ("completed",       False, "engine_unavailable",  "启动已结束，引擎异常"),
            ("failed",          True,  "failed",              "引擎已启动，初始化失败"),
            ("failed",          False, "failed",              "失败"),
        ],
    )
    @pytest.mark.asyncio
    async def test_lookup(
        self,
        manager: EngineManager,
        phase: str,
        running: bool,
        expected_state: str,
        expected_message: str,
    ):
        manager._init_phase = phase
        manager._process = _make_process(running)
        result = await manager.readiness()
        assert result == {"state": expected_state, "message": expected_message}

    @pytest.mark.asyncio
    async def test_no_process_treated_as_dead(self, manager: EngineManager):
        # _process is None on a fresh manager; readiness() must not crash and
        # should treat liveness as False (the "dead" column).
        manager._init_phase = "completed"
        manager._process = None
        result = await manager.readiness()
        assert result["state"] == "engine_unavailable"
        assert result["message"] == "启动已结束，引擎异常"

    @pytest.mark.asyncio
    async def test_is_running_raising_treated_as_dead(self, manager: EngineManager):
        proc = MagicMock()
        proc.is_running = AsyncMock(side_effect=RuntimeError("boom"))
        manager._process = proc
        manager._init_phase = "completed"
        result = await manager.readiness()
        assert result["state"] == "engine_unavailable"

    @pytest.mark.asyncio
    async def test_unknown_phase_falls_back_to_starting(self, manager: EngineManager):
        manager._init_phase = "this_phase_does_not_exist"
        manager._process = _make_process(True)
        result = await manager.readiness()
        # Defensive default — better than crashing the endpoint.
        assert result["state"] == "starting"


class TestLifecyclePhaseTransitions:
    @pytest.mark.asyncio
    async def test_initialize_reaches_completed(
        self, manager: EngineManager, patch_lifecycle_deps
    ):
        await manager.initialize()
        assert manager._init_phase == "completed"
        result = await manager.readiness()
        assert result["state"] == "ready"

    @pytest.mark.asyncio
    async def test_failed_when_activation_raises(
        self, registry: EngineRegistry, patch_lifecycle_deps
    ):
        class _ExplodingEngine(BaseEngine):
            name = "boom"
            version = "0.0.1"
            _CAPS = EngineCapabilities(supported={Capability.SESSION_LIST})

            @property
            def capabilities(self) -> EngineCapabilities:
                return self._CAPS

            async def initialize(self) -> None:
                raise RuntimeError("kaboom")

            async def shutdown(self) -> None: ...

        registry.register(_ExplodingEngine)
        EngineManager.reset_instance()
        mgr = EngineManager("boom", registry=registry)

        with pytest.raises(RuntimeError, match="kaboom"):
            await mgr.initialize()

        assert mgr._init_phase == "failed"
        result = await mgr.readiness()
        assert result["state"] == "failed"

    @pytest.mark.asyncio
    async def test_start_attempted_set_after_process_start(
        self, manager: EngineManager, patch_lifecycle_deps
    ):
        # Capture the phase right after process.start() but before activation.
        observed: list[str] = []

        async def slow_activate(target: str) -> None:
            observed.append(manager._init_phase)

        with patch.object(manager, "_activate_engine", slow_activate):
            await manager.initialize()

        assert observed == ["start_attempted"]
        assert manager._init_phase == "completed"


class TestReadinessEndpoint:
    @pytest.mark.asyncio
    async def test_endpoint_returns_state_and_message(self, manager: EngineManager):
        from engine.community.api import app as app_module
        from fastapi.testclient import TestClient

        manager._init_phase = "completed"
        manager._process = _make_process(True)

        with patch.object(
            EngineManager, "get_instance", classmethod(lambda cls: manager)
        ):
            client = TestClient(app_module.app)
            resp = client.get("/readiness")
            assert resp.status_code == 200
            body = resp.json()
            assert set(body.keys()) == {"state", "message"}
            assert body["state"] == "ready"
