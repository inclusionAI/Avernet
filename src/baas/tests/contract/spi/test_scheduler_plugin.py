import asyncio

import pytest

from secbaas.community.plugins.scheduler.stub import StubSchedulerPlugin
from secbaas.community.spi.scheduler import SchedulerPlugin


class SchedulerPluginContract:
    plugin: SchedulerPlugin

    def test_start_stop_lifecycle(self) -> None:
        self.plugin.start()
        self.plugin.stop()

    def test_trigger_now_no_job(self) -> None:
        self.plugin.trigger_now()


class TestStubSchedulerPlugin(SchedulerPluginContract):
    def setup_method(self) -> None:
        self.plugin = StubSchedulerPlugin()


class TestApsSchedulerPluginConformance(SchedulerPluginContract):
    @pytest.fixture(autouse=True)
    def _skip_without_apscheduler(self) -> None:
        try:
            import apscheduler  # noqa: F401
        except ImportError:
            pytest.skip("APScheduler not installed")

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self) -> None:
        from secbaas.community.plugins.scheduler.real import ApsSchedulerPlugin

        plugin = ApsSchedulerPlugin(job_func=lambda: None, interval_seconds=99999)
        plugin.start()
        plugin.stop()

    @pytest.mark.asyncio
    async def test_trigger_now_no_job(self) -> None:
        from secbaas.community.plugins.scheduler.real import ApsSchedulerPlugin

        plugin = ApsSchedulerPlugin(job_func=lambda: None, interval_seconds=99999)
        plugin.trigger_now()


class TestStubSchedulerWithJob:
    @pytest.mark.asyncio
    async def test_trigger_now_async(self) -> None:
        called = False

        async def job() -> None:
            nonlocal called
            called = True

        plugin = StubSchedulerPlugin(job_func=job)
        plugin.trigger_now()
        await asyncio.sleep(0.1)
        assert called is True
