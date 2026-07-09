"""Tests for bootstrap/_cron.py — CronLifecycle unit tests.

Covers:
- Start path: registers and starts AppScheduler
- Exception isolation: start() / stop() never raises
- Singleton semantics via ApplicationContainer
"""

from unittest.mock import MagicMock

import pytest

from secbaas.bootstrap._cron import CronLifecycle


def _make_cron_lifecycle(**overrides):
    """Build a CronLifecycle with sensible defaults for testing."""
    defaults = {
        "app_scheduler": MagicMock(),
        "tasks": [MagicMock(), MagicMock()],
    }
    defaults.update(overrides)
    return CronLifecycle(**defaults)


# ---------------------------------------------------------------------------
# CronLifecycle.start
# ---------------------------------------------------------------------------


class TestCronLifecycleStart:
    """Tests for CronLifecycle.start()."""

    @pytest.mark.asyncio
    async def test_starts_scheduler(self):
        """THEN add_task is called for each task and scheduler starts."""
        mock_scheduler = MagicMock()
        task_a = MagicMock()
        task_b = MagicMock()
        cl = _make_cron_lifecycle(
            app_scheduler=mock_scheduler,
            tasks=[task_a, task_b],
        )
        await cl.start()

        assert mock_scheduler.add_task.call_count == 2
        mock_scheduler.add_task.assert_any_call(task_a)
        mock_scheduler.add_task.assert_any_call(task_b)
        mock_scheduler.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_swallows_exception(self):
        """WHEN AppScheduler.add_task raises, THEN start() returns normally."""
        mock_scheduler = MagicMock()
        mock_scheduler.add_task.side_effect = RuntimeError("add failed")
        cl = _make_cron_lifecycle(app_scheduler=mock_scheduler)
        await cl.start()  # Should not raise


# ---------------------------------------------------------------------------
# CronLifecycle.stop
# ---------------------------------------------------------------------------


class TestCronLifecycleStop:
    """Tests for CronLifecycle.stop()."""

    @pytest.mark.asyncio
    async def test_stop_calls_scheduler_stop(self):
        """WHEN stop() is called, THEN AppScheduler.stop() is called."""
        mock_scheduler = MagicMock()
        cl = _make_cron_lifecycle(app_scheduler=mock_scheduler)
        await cl.stop()

        mock_scheduler.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_swallows_scheduler_stop_exception(self):
        """WHEN scheduler.stop() raises, THEN stop() returns normally."""
        mock_scheduler = MagicMock()
        mock_scheduler.stop.side_effect = RuntimeError("stop failed")
        cl = _make_cron_lifecycle(app_scheduler=mock_scheduler)
        await cl.stop()  # Should not raise


# ---------------------------------------------------------------------------
# Singleton semantics
# ---------------------------------------------------------------------------


class TestCronLifecycleSingleton:
    """Tests for ApplicationContainer.cron_lifecycle Singleton semantics."""

    def test_singleton_resolves_same_instance(self):
        """WHEN CronLifecycle is used as a Singleton provider, THEN resolving
        twice returns the same instance."""
        from dependency_injector import containers, providers

        class TestContainer(containers.DeclarativeContainer):
            cron_lifecycle = providers.Singleton(
                CronLifecycle,
                app_scheduler=providers.Object(MagicMock()),
                tasks=providers.List(providers.Object(MagicMock())),
            )

        container = TestContainer()
        instance1 = container.cron_lifecycle()
        instance2 = container.cron_lifecycle()
        assert instance1 is instance2
