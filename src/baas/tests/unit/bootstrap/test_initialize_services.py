"""Tests for bootstrap/_container.py — initialize_services / shutdown_services.

Covers:
- Lifecycle components are started in order during initialize_services
- Lifecycle components are stopped in reverse order during shutdown_services
- Error propagation: a component start failure raises RuntimeError
- Shutdown resilience: component stop failures don't block remaining stops
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_lifecycle_mock(name: str) -> MagicMock:
    """Build a mock that satisfies the Lifecycle Protocol."""
    mock = MagicMock(name=name)
    mock.start = AsyncMock(name=f"{name}.start")
    mock.stop = AsyncMock(name=f"{name}.stop")
    return mock


def _make_mock_container():
    """Build a mock ApplicationContainer with lifecycle_components."""
    cm = _make_lifecycle_mock("ConnectionManager")
    ir = _make_lifecycle_mock("InstanceRouter")
    wr = _make_lifecycle_mock("WorkerRouter")
    cron = _make_lifecycle_mock("CronLifecycle")
    worker = _make_lifecycle_mock("BotRequestWorker")
    lpm = _make_lifecycle_mock("LocalProcessManagerLifecycle")
    db = _make_lifecycle_mock("DatabaseManagerLifecycle")

    components = [cm, ir, wr, cron, worker, lpm, db]

    container = MagicMock(
        config=MagicMock(from_dict=MagicMock()),
        services=MagicMock(),
        init_resources=MagicMock(),
        cron_lifecycle=MagicMock(return_value=cron),
        lifecycle_components=MagicMock(return_value=components),
    )
    return container, components


class TestInitializeServicesOrder:
    """Tests for lifecycle component start order during initialize_services."""

    @pytest.mark.asyncio
    async def test_components_started_in_list_order(self):
        """WHEN initialize_services runs, THEN each component's start() is called
        in the order defined by lifecycle_components."""
        from secbaas.community.bootstrap._container import initialize_services

        container, components = _make_mock_container()
        call_order = []

        for comp in components:
            original_start = comp.start

            async def track_start(_name=comp._mock_name, _orig=original_start):
                call_order.append(_name)
                await _orig()

            comp.start = track_start

        with patch("secbaas.community.bootstrap._container._resolve_all_providers"):
            await initialize_services(container)

        expected_order = [c._mock_name for c in components]
        assert call_order == expected_order

    @pytest.mark.asyncio
    async def test_all_components_started(self):
        """WHEN initialize_services runs, THEN start() is called on every component."""
        from secbaas.community.bootstrap._container import initialize_services

        container, components = _make_mock_container()

        with patch("secbaas.community.bootstrap._container._resolve_all_providers"):
            await initialize_services(container)

        for comp in components:
            comp.start.assert_awaited_once()


class TestShutdownServicesOrder:
    """Tests for lifecycle component stop order during shutdown_services."""

    @pytest.mark.asyncio
    async def test_components_stopped_in_reverse_order(self):
        """WHEN shutdown_services runs, THEN stop() is called in reverse order."""
        from secbaas.community.bootstrap._container import shutdown_services

        container, components = _make_mock_container()
        call_order = []

        for comp in components:
            original_stop = comp.stop

            async def track_stop(_name=comp._mock_name, _orig=original_stop):
                call_order.append(_name)
                await _orig()

            comp.stop = track_stop

        await shutdown_services(container)

        expected_order = [c._mock_name for c in reversed(components)]
        assert call_order == expected_order

    @pytest.mark.asyncio
    async def test_all_components_stopped(self):
        """WHEN shutdown_services runs, THEN stop() is called on every component."""
        from secbaas.community.bootstrap._container import shutdown_services

        container, components = _make_mock_container()

        await shutdown_services(container)

        for comp in components:
            comp.stop.assert_awaited_once()


class TestShutdownResilience:
    """Tests for shutdown resilience — component failures don't block remaining stops."""

    @pytest.mark.asyncio
    async def test_shutdown_resilient_to_component_failure(self):
        """WHEN a component's stop() raises, THEN remaining components are still stopped."""
        from secbaas.community.bootstrap._container import shutdown_services

        container, components = _make_mock_container()
        # Make the second component's stop raise
        components[1].stop = AsyncMock(side_effect=RuntimeError("stop failed"))

        # Should not raise
        await shutdown_services(container)

        # All other components should still be stopped
        components[0].stop.assert_awaited_once()
        components[2].stop.assert_awaited_once()
        components[3].stop.assert_awaited_once()
        components[4].stop.assert_awaited_once()
        components[5].stop.assert_awaited_once()
        components[6].stop.assert_awaited_once()


class TestInitializeServicesErrorPropagation:
    """Tests for error propagation from initialize_services."""

    @pytest.mark.asyncio
    async def test_component_start_failure_raises(self):
        """WHEN a component's start() fails, THEN RuntimeError is raised."""
        from secbaas.community.bootstrap._container import initialize_services

        container, components = _make_mock_container()
        components[0].start = AsyncMock(side_effect=RuntimeError("start failed"))

        with patch("secbaas.community.bootstrap._container._resolve_all_providers"):
            with pytest.raises(RuntimeError, match="Failed to start"):
                await initialize_services(container)

    @pytest.mark.asyncio
    async def test_component_start_failure_stops_further_starts(self):
        """WHEN a component's start() fails, THEN subsequent components are NOT started."""
        from secbaas.community.bootstrap._container import initialize_services

        container, components = _make_mock_container()
        components[1].start = AsyncMock(side_effect=RuntimeError("start failed"))

        with patch("secbaas.community.bootstrap._container._resolve_all_providers"):
            with pytest.raises(RuntimeError):
                await initialize_services(container)

        # First component started, second failed, rest should not have been called
        components[0].start.assert_awaited_once()
        components[2].start.assert_not_awaited()
