import asyncio

import pytest

from secbaas.community.plugins.scheduler.stub import StubSchedulerPlugin


@pytest.fixture
def plugin() -> StubSchedulerPlugin:
    return StubSchedulerPlugin()


def test_import(plugin: StubSchedulerPlugin) -> None:
    assert isinstance(plugin, StubSchedulerPlugin)


def test_start_stop_lifecycle(plugin: StubSchedulerPlugin) -> None:
    plugin.start()
    plugin.stop()


@pytest.mark.asyncio
async def test_trigger_now_calls_callback() -> None:
    called = False

    async def my_job() -> None:
        nonlocal called
        called = True

    plugin = StubSchedulerPlugin(job_func=my_job)
    plugin.trigger_now()
    await asyncio.sleep(0.1)
    assert called is True


@pytest.mark.asyncio
async def test_trigger_now_sync_callback() -> None:
    results: list[int] = []

    def my_job() -> None:
        results.append(1)

    plugin = StubSchedulerPlugin(job_func=my_job)
    plugin.trigger_now()
    assert results == [1]
