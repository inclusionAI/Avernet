"""Dedicated manager-sync loop lifecycle."""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_collaborator.manager_sync import (
    lifecycle as lifecycle_module,
)
from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    BotManagerSyncDispatcher,
    DisabledBotManagerSync,
    DispatcherPolicy,
)
from agentclaw.community.core.bot_collaborator.manager_sync.lifecycle import (
    BotManagerSyncLifecycle,
    DisabledBotManagerSyncLifecycle,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SpaceChange,
    SubmissionResult,
    SyncKey,
    SyncOutcome,
    SyncReason,
)
from agentclaw.community.plugins.local.bcs_managers import RecordingBcsManagersPlugin


class RecordingService:
    def __init__(self) -> None:
        self.calls: list[SyncKey] = []

    async def sync_once(self, key):
        self.calls.append(key)
        return SyncOutcome.CONFIRMED


class LoopCapturingBcs(RecordingBcsManagersPlugin):
    def __init__(self) -> None:
        super().__init__()
        self.aclose_loop = None

    async def aclose(self) -> None:
        self.aclose_loop = asyncio.get_running_loop()


def submit(dispatcher, bot_pk=7):
    return dispatcher.request_sync(
        bot_pk=bot_pk,
        env="dev",
        avernet_tenant="a",
        reason=SyncReason.COLLABORATOR_CHANGED,
    )


def loop_thread_alive() -> bool:
    return any(thread.name == "manager-sync-loop" for thread in threading.enumerate())


def wait_loop_thread_gone(timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not loop_thread_alive():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.asyncio
async def test_workers_and_delivery_pool_live_on_dedicated_loop():
    service = RecordingService()
    bcs = LoopCapturingBcs()
    dispatcher = BotManagerSyncDispatcher(
        service, Mock(), policy=DispatcherPolicy(worker_count=4)
    )
    lifecycle = BotManagerSyncLifecycle(dispatcher, bcs)
    await lifecycle.startup()
    assert loop_thread_alive()
    try:
        assert submit(dispatcher) is SubmissionResult.QUEUED
        assert await dispatcher.drain(5)
        assert service.calls == [SyncKey("a", "dev", 7)]
    finally:
        await lifecycle.shutdown()

    assert wait_loop_thread_gone()
    assert bcs.aclose_loop is not None
    assert bcs.aclose_loop is not asyncio.get_running_loop()
    assert submit(dispatcher, 8) is SubmissionResult.REJECTED
    await lifecycle.shutdown()


@pytest.mark.asyncio
async def test_concurrent_startup_starts_background_loop_at_most_once(monkeypatch):
    dispatcher = BotManagerSyncDispatcher(RecordingService(), Mock())
    lifecycle = BotManagerSyncLifecycle(dispatcher, LoopCapturingBcs())
    original_start = lifecycle._start_background
    entered = threading.Event()
    release = threading.Event()
    start_calls = 0

    def gated_start() -> None:
        nonlocal start_calls
        start_calls += 1
        entered.set()
        assert release.wait(5)
        original_start()

    monkeypatch.setattr(lifecycle, "_start_background", gated_start)
    first = asyncio.create_task(lifecycle.startup())
    assert await asyncio.to_thread(entered.wait, 5)
    second = asyncio.create_task(lifecycle.startup())
    await second

    assert start_calls == 1
    release.set()
    await first
    try:
        assert dispatcher.stats.attached
    finally:
        await lifecycle.shutdown()
    assert wait_loop_thread_gone()


@pytest.mark.asyncio
async def test_shutdown_waits_for_in_progress_startup_before_stopping(monkeypatch):
    dispatcher = BotManagerSyncDispatcher(RecordingService(), Mock())
    lifecycle = BotManagerSyncLifecycle(dispatcher, LoopCapturingBcs())
    original_start = lifecycle._start_background
    entered = threading.Event()
    release = threading.Event()

    def gated_start() -> None:
        entered.set()
        assert release.wait(5)
        original_start()

    monkeypatch.setattr(lifecycle, "_start_background", gated_start)
    startup = asyncio.create_task(lifecycle.startup())
    assert await asyncio.to_thread(entered.wait, 5)
    shutdown = asyncio.create_task(lifecycle.shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done()
    assert submit(dispatcher) is SubmissionResult.REJECTED

    release.set()
    await asyncio.gather(startup, shutdown)

    assert wait_loop_thread_gone()
    assert dispatcher.stats.closed
    assert not dispatcher.stats.attached


@pytest.mark.asyncio
async def test_cancelled_startup_then_shutdown_does_not_orphan_loop(monkeypatch):
    dispatcher = BotManagerSyncDispatcher(RecordingService(), Mock())
    lifecycle = BotManagerSyncLifecycle(dispatcher, LoopCapturingBcs())
    original_start = lifecycle._start_background
    entered = threading.Event()
    release = threading.Event()

    def gated_start() -> None:
        entered.set()
        assert release.wait(5)
        original_start()

    monkeypatch.setattr(lifecycle, "_start_background", gated_start)
    startup = asyncio.create_task(lifecycle.startup())
    assert await asyncio.to_thread(entered.wait, 5)
    startup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await startup

    shutdown = asyncio.create_task(lifecycle.shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done()
    assert submit(dispatcher) is SubmissionResult.REJECTED
    release.set()
    await asyncio.wait_for(shutdown, 5)

    assert wait_loop_thread_gone()
    assert dispatcher.stats.closed
    assert not dispatcher.stats.attached


@pytest.mark.asyncio
async def test_foreign_loop_cannot_stop_worker_owned_dispatcher():
    dispatcher = BotManagerSyncDispatcher(RecordingService(), Mock())
    lifecycle = BotManagerSyncLifecycle(dispatcher, LoopCapturingBcs())
    await lifecycle.startup()
    try:
        with pytest.raises(RuntimeError, match="worker loop"):
            await dispatcher.stop()
        assert dispatcher.stats.attached
    finally:
        await lifecycle.shutdown()


@pytest.mark.asyncio
async def test_start_failure_only_disables_syncer(monkeypatch, caplog):
    dispatcher = BotManagerSyncDispatcher(RecordingService(), Mock())

    async def fail_attach(self):
        raise RuntimeError("attach failed")

    monkeypatch.setattr(BotManagerSyncDispatcher, "attach", fail_attach)
    lifecycle = BotManagerSyncLifecycle(dispatcher, LoopCapturingBcs())
    await lifecycle.startup()
    assert wait_loop_thread_gone()
    assert submit(dispatcher) is SubmissionResult.REJECTED
    assert sum("startup_failed" in record.message for record in caplog.records) == 1
    assert lifecycle._started is False
    assert lifecycle._startup_done.is_set()

    await lifecycle.startup()
    assert sum("startup_failed" in record.message for record in caplog.records) == 1
    assert not loop_thread_alive()
    await lifecycle.shutdown()


@pytest.mark.asyncio
async def test_loop_readiness_timeout_cannot_leave_an_unowned_thread(monkeypatch):
    original_new_event_loop = asyncio.new_event_loop

    def delayed_new_event_loop():
        time.sleep(0.05)
        return original_new_event_loop()

    monkeypatch.setattr(asyncio, "new_event_loop", delayed_new_event_loop)
    monkeypatch.setattr(lifecycle_module, "_LOOP_READY_TIMEOUT_SECONDS", 0.01)
    dispatcher = BotManagerSyncDispatcher(RecordingService(), Mock())
    lifecycle = BotManagerSyncLifecycle(dispatcher, LoopCapturingBcs())

    await lifecycle.startup()

    assert submit(dispatcher) is SubmissionResult.REJECTED
    assert wait_loop_thread_gone()


@pytest.mark.asyncio
async def test_shutdown_is_bounded_and_cancels_hung_async_round(monkeypatch):
    entered = threading.Event()
    cancelled = threading.Event()

    class HungService:
        async def sync_once(self, key):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    dispatcher = BotManagerSyncDispatcher(HungService(), Mock())
    lifecycle = BotManagerSyncLifecycle(dispatcher, LoopCapturingBcs())
    await lifecycle.startup()
    assert submit(dispatcher) is SubmissionResult.QUEUED
    assert entered.wait(5)
    monkeypatch.setattr(lifecycle_module, "_SHUTDOWN_DRAIN_SECONDS", 0.05)
    await lifecycle.shutdown()
    assert cancelled.wait(5)
    assert wait_loop_thread_gone()
    assert dispatcher.stats.closed
    assert not dispatcher.stats.attached


@pytest.mark.asyncio
async def test_disabled_lifecycle_and_protocol_use_zero_resources():
    disabled = DisabledBotManagerSync()
    lifecycle = DisabledBotManagerSyncLifecycle("disabled")
    await lifecycle.bootstrap()
    await lifecycle.startup()
    await lifecycle.shutdown()
    await lifecycle.teardown()
    assert submit(disabled) is SubmissionResult.DISABLED
    assert (
        disabled.request_space_sync(SpaceChange("a", "dev", ("space",), ()))
        is SubmissionResult.DISABLED
    )
    assert not loop_thread_alive()


def test_lifecycle_requires_real_dispatcher():
    with pytest.raises(TypeError, match="real dispatcher"):
        BotManagerSyncLifecycle(Mock(), LoopCapturingBcs())
