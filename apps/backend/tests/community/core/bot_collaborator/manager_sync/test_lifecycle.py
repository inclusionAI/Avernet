"""Unit tests for the bot manager sync lifecycle (dedicated loop owner).

Task 6 pins the process-level posture:

- One dedicated thread hosts exactly one asyncio loop; the four workers
  and every ``AsyncClient`` live and die on it — ``bcs.aclose()`` runs
  on that loop at shutdown, never on the app loop.
- Startup never blocks on BCS availability and never aborts the backend
  boot: a failed start only stops the syncer (admission REJECTED) and
  alerts. Structural config errors are a different, earlier failure.
- Shutdown stops admission first, drains bounded, ends the loop and
  thread, and never clears unknown markers (a cancelled round keeps
  them; closing the HTTP pool cannot convert unknown into success).
- The disabled posture holds zero resources: no thread, no loop.
"""
from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import Mock

import pytest

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
    SubmissionResult,
    SyncKey,
    SyncReason,
)
from agentclaw.community.plugins.local.bcs_managers import (
    RecordingBcsManagersPlugin,
)
from agentclaw.community.plugins.local.manager_sync_coordination import (
    MemoryManagerSyncCoordinator,
)
from agentclaw.community.core.bot_collaborator.manager_sync.service import (
    BotManagerSyncService,
)


class FlakyService:
    """Immediately-confirmed service double (no gates needed here)."""

    def __init__(self) -> None:
        self.sync_once_calls: list[SyncKey] = []
        self.hold_calls: list[SyncKey] = []

    async def sync_once(self, key: SyncKey):
        self.sync_once_calls.append(key)
        from agentclaw.community.kernel.bot_manager_sync import SyncOutcome

        return SyncOutcome.CONFIRMED

    async def hold_owner_check(self, key: SyncKey):
        self.hold_calls.append(key)
        from agentclaw.community.kernel.bot_manager_sync import SyncOutcome

        return SyncOutcome.BLOCKED


class LoopCapturingBcs(RecordingBcsManagersPlugin):
    """Recording fake that also records the loop ``aclose`` ran on."""

    def __init__(self) -> None:
        super().__init__()
        self.aclose_loop: asyncio.AbstractEventLoop | None = None

    async def aclose(self) -> None:
        self.aclose_loop = asyncio.get_running_loop()


def no_manager_sync_threads() -> bool:
    return not any(t.name.startswith("manager-sync") for t in threading.enumerate())


def _wait_thread_gone(name: str, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(t.name == name for t in threading.enumerate()):
            return True
        time.sleep(0.01)
    return False


# -- the dedicated loop owner ---------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_runs_workers_on_dedicated_loop_and_closes_there():
    service = FlakyService()
    bcs = LoopCapturingBcs()
    dispatcher = BotManagerSyncDispatcher(
        service, Mock(), policy=DispatcherPolicy(worker_count=4)
    )
    lifecycle = BotManagerSyncLifecycle(dispatcher, bcs)
    await lifecycle.startup()
    thread = next(
        (t for t in threading.enumerate() if t.name == "manager-sync-loop"), None
    )
    assert thread is not None and thread.is_alive()

    try:
        assert (
            dispatcher.request_sync(
                bot_pk=7, env="dev", avernet_tenant="a",
                reason=SyncReason.COLLABORATOR_CHANGED,
            )
            is SubmissionResult.QUEUED
        )
        # Cross-loop drain from the test's own loop must observe the
        # dedicated loop finishing the round.
        assert await dispatcher.drain(5.0) is True
        assert service.sync_once_calls == [SyncKey("a", "dev", 7)]
        assert bcs.calls == []  # startup never touches BCS
    finally:
        await lifecycle.shutdown()

    await lifecycle.shutdown()  # idempotent
    assert dispatcher.stats.closed is True
    assert (
        dispatcher.request_sync(
            bot_pk=8, env="dev", avernet_tenant="a",
            reason=SyncReason.COLLABORATOR_CHANGED,
        )
        is SubmissionResult.REJECTED
    )
    assert dispatcher.stats.attached is False
    assert _wait_thread_gone("manager-sync-loop") is True
    assert no_manager_sync_threads() is True
    # aclose ran on the dedicated worker loop, not on this test loop.
    assert bcs.aclose_loop is not None
    assert bcs.aclose_loop is not asyncio.get_running_loop()


@pytest.mark.asyncio
async def test_stop_from_a_foreign_loop_is_refused():
    """Worker-owned resources may only be torn down on their own loop."""
    bcs = LoopCapturingBcs()
    dispatcher = BotManagerSyncDispatcher(
        FlakyService(), Mock(), policy=DispatcherPolicy(worker_count=1)
    )
    lifecycle = BotManagerSyncLifecycle(dispatcher, bcs)
    await lifecycle.startup()
    try:
        with pytest.raises(RuntimeError, match="worker loop"):
            await dispatcher.stop()
        # The refused stop must not have detached anything.
        assert dispatcher.stats.attached is True
    finally:
        await lifecycle.shutdown()
    assert _wait_thread_gone("manager-sync-loop") is True


@pytest.mark.asyncio
async def test_start_failure_stops_only_the_syncer_and_alerts(
    monkeypatch, caplog
):
    service = FlakyService()
    dispatcher = BotManagerSyncDispatcher(
        service, Mock(), policy=DispatcherPolicy(worker_count=4)
    )

    async def broken_attach(self) -> None:
        raise RuntimeError("attach exploded")

    monkeypatch.setattr(BotManagerSyncDispatcher, "attach", broken_attach)
    lifecycle = BotManagerSyncLifecycle(dispatcher, LoopCapturingBcs())

    # The backend keeps starting: startup swallows the failure.
    await lifecycle.startup()
    try:
        assert any(
            "MANAGER-SYNC" in record.message and "unavailable" in record.message
            for record in caplog.records
        ), "startup failure must alert"
        # Partial resources are cleaned up — no lingering thread.
        assert _wait_thread_gone("manager-sync-loop") is True
        assert no_manager_sync_threads() is True
        # The syncer is "unavailable": admission is closed.
        assert (
            dispatcher.request_sync(
                bot_pk=7, env="dev", avernet_tenant="a",
                reason=SyncReason.COLLABORATOR_CHANGED,
            )
            is SubmissionResult.REJECTED
        )
        assert service.sync_once_calls == []
    finally:
        await lifecycle.shutdown()


# -- the disabled posture ---------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_lifecycle_and_protocol_hold_no_resources():
    disabled_entry = DisabledBotManagerSync()
    disabled_lifecycle = DisabledBotManagerSyncLifecycle(reason="disabled")
    assert isinstance(disabled_lifecycle, BotManagerSyncLifecycle)

    # All four hooks are safe no-ops and resolve instantly.
    await disabled_lifecycle.bootstrap()
    await disabled_lifecycle.startup()
    await disabled_lifecycle.shutdown()
    await disabled_lifecycle.teardown()

    # Submission is a flat DISABLED with zero resources behind it.
    assert (
        disabled_entry.request_sync(
            bot_pk=7, env="dev", avernet_tenant="a",
            reason=SyncReason.COLLABORATOR_CHANGED,
        )
        is SubmissionResult.DISABLED
    )
    from agentclaw.community.kernel.bot_manager_sync import SpaceChange

    assert (
        disabled_entry.request_space_sync(
            SpaceChange("a", "dev", ("space:1",), ())
        )
        is SubmissionResult.DISABLED
    )
    assert no_manager_sync_threads() is True


# -- real service integration: unknown markers survive shutdown ------------------


@pytest.mark.asyncio
async def test_shutdown_never_clears_unknown_markers():
    """A round that ends UNKNOWN keeps its marker through shutdown.

    Closing the worker loop / HTTP pool must never convert an unknown
    delivery into a confirmed one: the in-flight marker stays for the
    ops alignment flow (Task 10), and no further send happens.
    """
    from agentclaw.community.kernel.bot_manager_sync import (
        CollaboratorFact,
        DeliveryFailure,
        SyncFacts,
    )
    from agentclaw.community.plugin_api.bcs_managers import BcsManagersError

    key = SyncKey("a", "dev", 7)
    reader = Mock()
    reader.read_facts.return_value = SyncFacts(
        key, "bot", "entity", "owner",
        (CollaboratorFact("admin-1", "admin", True),),
    )
    bcs = RecordingBcsManagersPlugin()
    bcs.outcomes.append(BcsManagersError(DeliveryFailure.UNKNOWN, "read_timeout"))
    coordinator = MemoryManagerSyncCoordinator()
    service = BotManagerSyncService(reader=reader, bcs=bcs, coordinator=coordinator)
    dispatcher = BotManagerSyncDispatcher(service, reader)
    lifecycle = BotManagerSyncLifecycle(dispatcher, bcs)

    await lifecycle.startup()
    try:
        assert (
            dispatcher.request_sync(
                bot_pk=7, env="dev", avernet_tenant="a",
                reason=SyncReason.COLLABORATOR_CHANGED,
            )
            is SubmissionResult.QUEUED
        )
        assert await dispatcher.drain(5.0) is True
        # The one PUT ended UNKNOWN: the marker stays, no clear, no re-send.
        assert len(bcs.calls) == 1
        assert coordinator.read_blocked(key) is not None
    finally:
        await lifecycle.shutdown()
    assert _wait_thread_gone("manager-sync-loop") is True
    # Still set after the loop and pool are gone — shutdown never clears it.
    assert coordinator.read_blocked(key) is not None