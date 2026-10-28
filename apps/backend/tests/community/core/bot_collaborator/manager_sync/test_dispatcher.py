"""Unit tests for the bot manager sync dispatcher (bounded admission + workers).

Task 6 pins the memory-only admission and the OWNER_HANDOFF priority:

- ``request_sync`` / ``request_space_sync`` only touch memory — a hung
  service must never delay the submitting thread.
- Owner handoff latches a high-priority flag; ordinary notifications
  coalesce (generation++) and can never clear it — neither at admission
  nor at completion cleanup, where an UNKNOWN result from the old run
  must not swallow a handoff that arrived mid-flight.
- A failed hold never degrades to a roster send: the key stays
  owner-gated (sticky latch), retries are bounded inside the 15s defer
  window, and no hot loop remains afterwards.
- DEFERRED (another replica holds the claim) reschedules inside its
  defer budget; it is never mistaken for "someone else finished it".
- Capacity (``max_keys``) counts running entries and space hints;
  expansion stops at capacity and records the rejected remainder for
  the later repair sweep.
- No test waits real seconds: workers are Event-gated, deferrals use an
  injected fake clock plus explicit ``wake()`` calls.
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Sequence
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    BotManagerSyncDispatcher,
    DisabledBotManagerSync,
    DispatcherPolicy,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SpaceChange,
    SubmissionResult,
    SyncKey,
    SyncOutcome,
    SyncReason,
)
from agentclaw.community.utils.avernet_tenant import (
    DEFAULT_AVERNET_TENANT,
    get_current_avernet_tenant,
)


# -- shared fixtures / helpers -------------------------------------------------


class ScriptedService:
    """Blocking-by-gate service double with scripted, FIFO outcomes.

    ``hold_owner_check`` / ``sync_once`` park on ``gate`` until the test
    releases it, then return the next scripted outcome (CONFIRMED by
    default for ``sync_once``, BLOCKED for ``hold_owner_check``).
    """

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.gate.set()
        self.sync_once_outcomes: list[SyncOutcome] = []
        self.hold_outcomes: list[SyncOutcome] = []
        self.sync_once_calls: list[SyncKey] = []
        self.hold_calls: list[SyncKey] = []
        self.tenants_seen: list[str] = []

    async def sync_once(self, key: SyncKey) -> SyncOutcome:
        self.sync_once_calls.append(key)
        self.tenants_seen.append(get_current_avernet_tenant())
        await self.gate.wait()
        return (
            self.sync_once_outcomes.pop(0)
            if self.sync_once_outcomes
            else SyncOutcome.CONFIRMED
        )

    async def hold_owner_check(self, key: SyncKey) -> SyncOutcome:
        self.hold_calls.append(key)
        self.tenants_seen.append(get_current_avernet_tenant())
        await self.gate.wait()
        return (
            self.hold_outcomes.pop(0)
            if self.hold_outcomes
            else SyncOutcome.BLOCKED
        )


class FakeClock:
    """Frozen monotonic clock the tests advance explicitly."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def make_dispatcher(
    service,
    reader=Mock(),
    *,
    policy: DispatcherPolicy | None = None,
    clock=None,
    env: str = "",
) -> BotManagerSyncDispatcher:
    return BotManagerSyncDispatcher(
        service, reader, policy=policy, clock=clock, env=env
    )


async def spin_until(predicate, *, timeout: float = 5.0, what: str = "") -> None:
    """Yield to the loop until ``predicate()`` is true.

    Real time is only a safety net against a dead loop; every worker
    step here is driven by events or explicit wakes, so the loop exits
    after a handful of ``sleep(0)`` yields.
    """
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, f"condition never became true: {what}"
        await asyncio.sleep(0)


# -- 1. admission is memory-only ----------------------------------------------


def test_request_does_not_read_database_or_network():
    service, reader = Mock(), Mock()
    dispatcher = BotManagerSyncDispatcher(service, reader, policy=DispatcherPolicy())
    result = dispatcher.request_sync(
        bot_pk=7,
        env="dev",
        avernet_tenant="a",
        reason=SyncReason.COLLABORATOR_CHANGED,
    )
    assert result is SubmissionResult.QUEUED
    reader.read_facts.assert_not_called()
    service.sync_once.assert_not_called()


def test_submission_before_start_is_bounded_memory_only():
    """Admission accepts a finite queue before the workers ever attach."""
    dispatcher = make_dispatcher(Mock(), Mock(), policy=DispatcherPolicy(max_keys=3))
    for pk in (1, 2, 3):
        assert (
            dispatcher.request_sync(
                bot_pk=pk, env="dev", avernet_tenant="a", reason=SyncReason.REPAIR
            )
            is SubmissionResult.QUEUED
        )
    assert (
        dispatcher.request_sync(
            bot_pk=4, env="dev", avernet_tenant="a", reason=SyncReason.REPAIR
        )
        is SubmissionResult.REJECTED
    )
    stats = dispatcher.stats
    assert stats.entries == 3
    assert stats.attached is False


def test_async_caller_gets_the_same_memory_only_admission():
    """The entry points are plain sync — usable from a coroutine as-is."""
    service = ScriptedService()
    dispatcher = make_dispatcher(service)
    assert (
        dispatcher.request_sync(
            bot_pk=1, env="dev", avernet_tenant="a", reason=SyncReason.REPAIR
        )
        is SubmissionResult.QUEUED
    )
    assert (
        dispatcher.request_space_sync(
            SpaceChange("a", "dev", ("space:1",), ())
        )
        is SubmissionResult.QUEUED
    )


def test_disabled_returns_disabled_and_holds_no_resources():
    disabled = DisabledBotManagerSync()
    assert (
        disabled.request_sync(
            bot_pk=7, env="dev", avernet_tenant="a", reason=SyncReason.REPAIR
        )
        is SubmissionResult.DISABLED
    )
    assert (
        disabled.request_space_sync(SpaceChange("a", "dev", ("s",), ()))
        is SubmissionResult.DISABLED
    )
    # No network/thread resources whatsoever — an empty instance dict.
    assert vars(disabled) == {}


# -- 2. generation / coalescing -------------------------------------------------


@pytest.mark.asyncio
async def test_same_key_notifications_coalesce_into_one_dispatch():
    service = ScriptedService()
    dispatcher = make_dispatcher(service, policy=DispatcherPolicy(worker_count=1))
    key = SyncKey("a", "dev", 7)
    assert (
        dispatcher.request_sync(bot_pk=7, env="dev", avernet_tenant="a",
                                reason=SyncReason.COLLABORATOR_CHANGED)
        is SubmissionResult.QUEUED
    )
    assert (
        dispatcher.request_sync(bot_pk=7, env="dev", avernet_tenant="a",
                                reason=SyncReason.COLLABORATOR_CHANGED)
        is SubmissionResult.COALESCED
    )
    await dispatcher.attach()
    try:
        assert await dispatcher.drain(2.0) is True
        assert service.sync_once_calls == [key]
        assert dispatcher.stats.entries == 0
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatcher_keeps_same_pk_tenants_separate():
    """Same Bot PK under two tenants = two entries, two dispatches."""
    service = ScriptedService()
    dispatcher = make_dispatcher(service, policy=DispatcherPolicy(worker_count=1))
    for tenant in ("t1", "t2"):
        assert (
            dispatcher.request_sync(bot_pk=7, env="dev", avernet_tenant=tenant,
                                    reason=SyncReason.COLLABORATOR_CHANGED)
            is SubmissionResult.QUEUED
        )
    await dispatcher.attach()
    try:
        assert await dispatcher.drain(2.0) is True
        assert set(service.sync_once_calls) == {
            SyncKey("t1", "dev", 7),
            SyncKey("t2", "dev", 7),
        }
        assert len(service.sync_once_calls) == 2
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_concurrent_thread_submissions_all_land_in_the_queue():
    service = ScriptedService()
    policy = DispatcherPolicy(worker_count=4)
    dispatcher = make_dispatcher(service, policy=policy)
    per_thread, threads_count = 25, 8
    start = threading.Barrier(threads_count + 1)
    results: list[SubmissionResult] = []
    results_lock = threading.Lock()

    def submitter(idx: int) -> None:
        start.wait()
        got = [
            dispatcher.request_sync(
                bot_pk=idx * per_thread + i + 1,
                env="dev",
                avernet_tenant="a",
                reason=SyncReason.REPAIR,
            )
            for i in range(per_thread)
        ]
        with results_lock:
            results.extend(got)

    threads = [
        threading.Thread(target=submitter, args=(i,), name=f"sync-submit-{i}")
        for i in range(threads_count)
    ]
    for t in threads:
        t.start()
    await dispatcher.attach()
    try:
        start.wait()
        for t in threads:
            t.join(timeout=5.0)
        assert all(t.is_alive() is False for t in threads)
        assert len(results) == per_thread * threads_count
        assert all(r is SubmissionResult.QUEUED for r in results)
        assert await dispatcher.drain(5.0) is True
        assert len(service.sync_once_calls) == per_thread * threads_count
        assert len(set(service.sync_once_calls)) == per_thread * threads_count
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_thread_submission_wakes_idle_workers_no_wait_lost():
    """A submission from a foreign thread must wake parked workers.

    Pins the single-wake-callback path: all four workers sit idle, a
    plain thread submits, and ``drain`` must observe the work completed
    — a lost wakeup would leave the queue frozen until the timeout.
    """
    service = ScriptedService()
    dispatcher = make_dispatcher(service, policy=DispatcherPolicy(worker_count=4))
    await dispatcher.attach()
    try:
        submitted = threading.Event()

        def submit_later() -> None:
            submitted.wait(timeout=5.0)
            for pk in (11, 12, 13):
                dispatcher.request_sync(
                    bot_pk=pk, env="dev", avernet_tenant="a", reason=SyncReason.REPAIR
                )

        # Let the workers park on the wake event before any entry exists.
        await asyncio.sleep(0)
        thread = threading.Thread(target=submit_later, name="sync-wake-submitter")
        thread.start()
        submitted.set()
        # Wait for the foreign-thread submissions BEFORE draining: a lost
        # wakeup would leave the queue frozen once the workers parked.
        thread.join(timeout=5.0)
        assert thread.is_alive() is False
        assert await dispatcher.drain(5.0) is True
        assert set(service.sync_once_calls) == {
            SyncKey("a", "dev", 11),
            SyncKey("a", "dev", 12),
            SyncKey("a", "dev", 13),
        }
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_burst_submissions_schedule_one_wake_callback_only():
    """A submission burst must not flood call_soon_threadsafe."""
    service = ScriptedService()
    dispatcher = make_dispatcher(
        service,
        policy=DispatcherPolicy(worker_count=1, max_keys=10_000),
    )
    await dispatcher.attach()
    try:
        loop = asyncio.get_running_loop()
        calls: list[object] = []
        original = loop.call_soon_threadsafe

        def counting_call_soon(callback, *args, **kwargs):
            calls.append(callback)
            return original(callback, *args, **kwargs)

        loop.call_soon_threadsafe = counting_call_soon  # type: ignore[method-assign]
        try:
            for pk in range(2_000):
                dispatcher.request_sync(
                    bot_pk=pk + 1, env="dev", avernet_tenant="a",
                    reason=SyncReason.REPAIR,
                )
        finally:
            loop.call_soon_threadsafe = original  # type: ignore[method-assign]
        # No await happened between submissions: exactly one wake is armed.
        assert len(calls) == 1
        assert await dispatcher.drain(5.0) is True
        assert len(service.sync_once_calls) == 2_000
    finally:
        await dispatcher.stop()


# -- 3. capacity: queue full counts running entries + space hints ----------------


@pytest.mark.asyncio
async def test_queue_full_rejects_bots_and_space_hints():
    """``max_keys`` counts entries (running included) plus space hints."""
    dispatcher = make_dispatcher(
        Mock(), Mock(), policy=DispatcherPolicy(worker_count=1, max_keys=3)
    )
    for pk in (1, 2, 3):
        assert (
            dispatcher.request_sync(
                bot_pk=pk, env="dev", avernet_tenant="a", reason=SyncReason.REPAIR
            )
            is SubmissionResult.QUEUED
        )
    assert (
        dispatcher.request_sync(
            bot_pk=4, env="dev", avernet_tenant="a", reason=SyncReason.REPAIR
        )
        is SubmissionResult.REJECTED
    )
    assert (
        dispatcher.request_space_sync(SpaceChange("a", "dev", ("space:1",), ()))
        is SubmissionResult.REJECTED
    )
    assert dispatcher.stats.entries == 3
    assert dispatcher.stats.rejected_space_keys == 0


@pytest.mark.asyncio
async def test_capacity_counts_running_entries():
    """A popped entry keeps occupying capacity while it runs."""
    service = ScriptedService()
    service.gate.clear()  # park the first dispatch inside the worker
    dispatcher = make_dispatcher(
        service, Mock(), policy=DispatcherPolicy(worker_count=1, max_keys=3)
    )
    await dispatcher.attach()
    try:
        assert (
            dispatcher.request_sync(
                bot_pk=1, env="dev", avernet_tenant="a", reason=SyncReason.REPAIR
            )
            is SubmissionResult.QUEUED
        )
        await spin_until(lambda: len(service.sync_once_calls) == 1,
                         what="first dispatch parked")
        # One entry is running — capacity left is 2, not 3.
        assert (
            dispatcher.request_sync(
                bot_pk=2, env="dev", avernet_tenant="a", reason=SyncReason.REPAIR
            )
            is SubmissionResult.QUEUED
        )
        assert (
            dispatcher.request_sync(
                bot_pk=3, env="dev", avernet_tenant="a", reason=SyncReason.REPAIR
            )
            is SubmissionResult.QUEUED
        )
        assert (
            dispatcher.request_sync(
                bot_pk=4, env="dev", avernet_tenant="a", reason=SyncReason.REPAIR
            )
            is SubmissionResult.REJECTED
        )
        service.gate.set()
        assert await dispatcher.drain(5.0) is True
    finally:
        await dispatcher.stop()


def test_capacity_counts_hints_against_the_same_total():
    """"""
    dispatcher = make_dispatcher(
        Mock(), Mock(), policy=DispatcherPolicy(worker_count=1, max_keys=2)
    )
    assert (
        dispatcher.request_sync(
            bot_pk=1, env="dev", avernet_tenant="a", reason=SyncReason.REPAIR
        )
        is SubmissionResult.QUEUED
    )
    hint_stats_oneshort = dispatcher.request_space_sync(
        SpaceChange("a", "dev", ("space:1",), ())
    )
    assert hint_stats_oneshort is SubmissionResult.QUEUED
    # entries(1) + hints(1) == max_keys(2): both a bot and a hint are refused.
    assert (
        dispatcher.request_sync(
            bot_pk=2, env="dev", avernet_tenant="a", reason=SyncReason.REPAIR
        )
        is SubmissionResult.REJECTED
    )
    assert (
        dispatcher.request_space_sync(SpaceChange("a", "dev", ("space:2",), ()))
        is SubmissionResult.REJECTED
    )


# -- 4. space hints: bounded expansion ------------------------------------------


class PagedReader:
    """Reader double serving ``space:1`` pages and recording the calls."""

    def __init__(self, pages: Sequence[Sequence[SyncKey]]) -> None:
        self.pages = list(pages)
        self.page_calls: list[tuple[SpaceChange, int, int]] = []

    def page_space_keys(self, change, *, after_pk: int, limit: int):
        self.page_calls.append((change, after_pk, limit))
        if after_pk == -1:
            return ()
        for page in self.pages:
            if page and page[-1].bot_pk > after_pk:
                return tuple(k for k in page if k.bot_pk > after_pk)
        return ()


@pytest.mark.asyncio
async def test_space_hint_expands_pages_until_exhausted():
    service = ScriptedService()
    reader = PagedReader(
        [
            (SyncKey("a", "dev", 7), SyncKey("a", "dev", 8)),
            (SyncKey("a", "dev", 9),),
        ]
    )
    dispatcher = make_dispatcher(
        service, reader, policy=DispatcherPolicy(worker_count=1, page_size=2)
    )
    change = SpaceChange("a", "dev", ("space:1",), ())
    assert dispatcher.request_space_sync(change) is SubmissionResult.QUEUED
    await dispatcher.attach()
    try:
        assert await dispatcher.drain(5.0) is True
        # Page cursors terminate: one page returns empty and the hint is done.
        assert [c[1] for c in reader.page_calls] == [0, 8, 9]
        assert all(c[2] == 2 for c in reader.page_calls)
        assert set(service.sync_once_calls) == {
            SyncKey("a", "dev", 7),
            SyncKey("a", "dev", 8),
            SyncKey("a", "dev", 9),
        }
        assert dispatcher.stats.rejected_space_keys == 0
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_space_expansion_stops_at_full_queue_and_records_rejected():
    """Paging stops once capacity fills; the dropped remainder is counted."""
    service = ScriptedService()
    reader = PagedReader(
        [
            (
                SyncKey("a", "dev", 21),
                SyncKey("a", "dev", 22),
                SyncKey("a", "dev", 23),
                SyncKey("a", "dev", 24),
                SyncKey("a", "dev", 25),
            )
        ]
    )
    dispatcher = make_dispatcher(
        service, reader, policy=DispatcherPolicy(worker_count=1, max_keys=3)
    )
    assert (
        dispatcher.request_space_sync(SpaceChange("a", "dev", ("space:1",), ()))
        is SubmissionResult.QUEUED
    )
    await dispatcher.attach()
    try:
        assert await dispatcher.drain(5.0) is True
        # Only one page read — expansion stops at capacity, never caches more.
        assert len(reader.page_calls) == 1
        assert dispatcher.stats.rejected_space_keys == 2
        assert set(service.sync_once_calls) == {
            SyncKey("a", "dev", 21),
            SyncKey("a", "dev", 22),
            SyncKey("a", "dev", 23),
        }
    finally:
        await dispatcher.stop()


# -- 5. env gate -----------------------------------------------------------------


def test_env_mismatch_is_refused_before_any_send():
    """"""
    service = ScriptedService()
    dispatcher = make_dispatcher(service, Mock(), env="dev")
    assert (
        dispatcher.request_sync(
            bot_pk=7, env="pre", avernet_tenant="a", reason=SyncReason.REPAIR
        )
        is SubmissionResult.REJECTED
    )
    assert (
        dispatcher.request_space_sync(SpaceChange("a", "pre", ("space:1",), ()))
        is SubmissionResult.REJECTED
    )
    assert (
        dispatcher.request_sync(
            bot_pk=7, env="dev", avernet_tenant="a", reason=SyncReason.REPAIR
        )
        is SubmissionResult.QUEUED
    )
    assert dispatcher.stats.entries == 1


# -- 6. tenant scope restoration ---------------------------------------------------


@pytest.mark.asyncio
async def test_worker_restores_tenant_scope_per_entry():
    """Each dispatch runs under its key's tenant; nothing leaks to idle runs."""
    service = ScriptedService()
    dispatcher = make_dispatcher(
        service, Mock(), policy=DispatcherPolicy(worker_count=1)
    )
    await dispatcher.attach()
    try:
        service.gate.clear()
        assert (
            dispatcher.request_sync(
                bot_pk=1, env="dev", avernet_tenant="tenant-a",
                reason=SyncReason.REPAIR,
            )
            is SubmissionResult.QUEUED
        )
        await spin_until(lambda: len(service.sync_once_calls) == 1,
                         what="first dispatch parked")
        # Inside the worker the tenant is bound; the test task itself is not.
        assert service.tenants_seen == ["tenant-a"]
        assert service.tenants_seen[-1] == "tenant-a"
        assert get_current_avernet_tenant() == DEFAULT_AVERNET_TENANT

        service.gate.set()
        assert await dispatcher.drain(2.0) is True
        assert (
            dispatcher.request_sync(
                bot_pk=2, env="dev", avernet_tenant="tenant-b",
                reason=SyncReason.REPAIR,
            )
            is SubmissionResult.QUEUED
        )
        await spin_until(lambda: len(service.sync_once_calls) == 2,
                         what="second dispatch started")
        assert service.tenants_seen == ["tenant-a", "tenant-b"]
        assert await dispatcher.drain(2.0) is True
    finally:
        await dispatcher.stop()


# -- 7. OWNER_HANDOFF priority (the brief's named tests) ----------------------------


@pytest.mark.asyncio
async def test_owner_handoff_survives_coalescing():
    """normal -> handoff -> normal: only the hold runs, sync_once never."""
    service = ScriptedService()
    dispatcher = make_dispatcher(
        service, Mock(), policy=DispatcherPolicy(worker_count=1)
    )
    key = SyncKey("a", "dev", 7)
    assert (
        dispatcher.request_sync(bot_pk=7, env="dev", avernet_tenant="a",
                               reason=SyncReason.COLLABORATOR_CHANGED)
        is SubmissionResult.QUEUED
    )
    assert (
        dispatcher.request_sync(bot_pk=7, env="dev", avernet_tenant="a",
                               reason=SyncReason.OWNER_HANDOFF)
        is SubmissionResult.COALESCED
    )
    assert (
        dispatcher.request_sync(bot_pk=7, env="dev", avernet_tenant="a",
                               reason=SyncReason.COLLABORATOR_CHANGED)
        is SubmissionResult.COALESCED
    )
    await dispatcher.attach()
    try:
        assert await dispatcher.drain(2.0) is True
        # The handoff flag latched through two ordinary coalesces: the one
        # dispatch that ran is the owner hold, never a roster send.
        assert service.hold_calls == [key]
        assert service.sync_once_calls == []
        assert dispatcher.stats.entries == 0
        assert dispatcher.stats.latched_owner_keys == 0
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_owner_handoff_arriving_during_run_is_not_dropped():
    """A handoff that lands mid-run is honoured even after an UNKNOWN run."""
    service = ScriptedService()
    service.sync_once_outcomes.append(SyncOutcome.UNKNOWN)
    service.gate.clear()
    dispatcher = make_dispatcher(
        service, Mock(), policy=DispatcherPolicy(worker_count=1)
    )
    key = SyncKey("a", "dev", 7)
    assert (
        dispatcher.request_sync(bot_pk=7, env="dev", avernet_tenant="a",
                               reason=SyncReason.COLLABORATOR_CHANGED)
        is SubmissionResult.QUEUED
    )
    await dispatcher.attach()
    try:
        await spin_until(lambda: len(service.sync_once_calls) == 1,
                         what="first dispatch parked")
        # Handoff lands while the old run is still in flight.
        assert (
            dispatcher.request_sync(bot_pk=7, env="dev", avernet_tenant="a",
                                    reason=SyncReason.OWNER_HANDOFF)
            is SubmissionResult.COALESCED
        )
        service.gate.set()  # old round finishes with UNKNOWN
        # The stale UNKNOWN must not clear the new handoff flag: the very
        # next dispatch on this key is the hold.
        await spin_until(lambda: len(service.hold_calls) == 1,
                         what="hold after unknown round")
        assert await dispatcher.drain(2.0) is True
        assert service.sync_once_calls == [key]
        assert service.hold_calls == [key]
        assert dispatcher.stats.entries == 0
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_handoff_arriving_during_hold_performs_no_roster_send():
    """A second handoff landing mid-hold survives the first hold's BLOCKED.

    H1's completion must NOT clear the flag to service the generation
    growth that H2's coalesce caused: the requeued entry re-runs the
    hold (planting the marker again is idempotent), and sync_once is
    never invoked for the key.
    """
    service = ScriptedService()
    service.gate.clear()  # park the H1 hold mid-run
    dispatcher = make_dispatcher(
        service, Mock(), policy=DispatcherPolicy(worker_count=1)
    )
    key = SyncKey("a", "dev", 7)
    assert (
        dispatcher.request_sync(bot_pk=7, env="dev", avernet_tenant="a",
                               reason=SyncReason.OWNER_HANDOFF)
        is SubmissionResult.QUEUED
    )
    await dispatcher.attach()
    try:
        await spin_until(lambda: len(service.hold_calls) == 1,
                         what="H1 hold in flight")
        # H2 coalesces while H1's hold is parked on the gate.
        assert (
            dispatcher.request_sync(bot_pk=7, env="dev", avernet_tenant="a",
                                    reason=SyncReason.OWNER_HANDOFF)
            is SubmissionResult.COALESCED
        )
        service.gate.set()  # H1 completes BLOCKED: marker planted
        # The conservative completion keeps the flag: the next dispatch
        # is a second hold, never a roster-send attempt.
        await spin_until(lambda: len(service.hold_calls) == 2,
                         what="requeued entry re-ran the hold")
        assert service.sync_once_calls == []
        assert await dispatcher.drain(2.0) is True
        assert service.hold_calls == [key, key]
        assert service.sync_once_calls == []  # no roster send, ever
        assert dispatcher.stats.entries == 0
        assert dispatcher.stats.latched_owner_keys == 0  # second hold resolved
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_owner_hold_failure_never_sends_roster():
    """Failed holds keep the key owner-gated; retries stay bounded inside
    the defer window; the ordinary path is never used as a fallback."""
    service = ScriptedService()
    service.hold_outcomes.extend([SyncOutcome.FAILED] * 32)
    clock = FakeClock()
    dispatcher = make_dispatcher(
        service, Mock(), policy=DispatcherPolicy(worker_count=1), clock=clock
    )
    assert (
        dispatcher.request_sync(bot_pk=7, env="dev", avernet_tenant="a",
                               reason=SyncReason.OWNER_HANDOFF)
        is SubmissionResult.QUEUED
    )
    await dispatcher.attach()
    try:
        # Attempts at fake times 1000, 1001, 1003, 1007, 1015 — the
        # 1s/2s/4s/8s backoff inside the 15s defer budget. The expiry of
        # the window drops the entry; the sticky owner latch stays.
        for attempt_no, step in enumerate((1.0, 2.0, 4.0, 8.0, None), start=1):
            await spin_until(
                lambda expected=attempt_no: len(service.hold_calls) == expected,
                what=f"hold attempt {attempt_no}",
            )
            assert service.sync_once_calls == []  # never a roster send
            if step is not None:
                clock.advance(step)
                dispatcher.wake()
        await spin_until(
            lambda: dispatcher.stats.entries == 0,
            what="failed-hold entry dropped after its window",
        )
        assert len(service.hold_calls) == 5  # bounded, no hot loop
        assert service.sync_once_calls == []
        assert dispatcher.stats.latched_owner_keys == 1  # sticky owner gate

        # A later ordinary notification on the latched key is still routed
        # to the hold, never to a roster send.
        assert (
            dispatcher.request_sync(bot_pk=7, env="dev", avernet_tenant="a",
                                    reason=SyncReason.COLLABORATOR_CHANGED)
            is SubmissionResult.QUEUED
        )
        await spin_until(lambda: len(service.hold_calls) == 6,
                         what="latched key routed to hold")
        assert service.sync_once_calls == []
    finally:
        await dispatcher.stop()


# -- 8. DEFERRED: bounded reschedule, never "someone else will finish" ----------------


@pytest.mark.asyncio
async def test_deferred_reschedules_inside_the_window_then_finishes():
    service = ScriptedService()
    service.sync_once_outcomes.append(SyncOutcome.DEFERRED)
    clock = FakeClock()
    dispatcher = make_dispatcher(
        service, Mock(), policy=DispatcherPolicy(worker_count=1), clock=clock
    )
    key = SyncKey("a", "dev", 7)
    assert (
        dispatcher.request_sync(bot_pk=7, env="dev", avernet_tenant="a",
                               reason=SyncReason.REPAIR)
        is SubmissionResult.QUEUED
    )
    await dispatcher.attach()
    try:
        await spin_until(lambda: len(service.sync_once_calls) == 1,
                         what="first (deferred) round")
        assert service.sync_once_outcomes == []  # DEFERRED consumed
        # The key is still queued — a DEFERRED is not a completion.
        assert dispatcher.stats.entries == 1
        clock.advance(1.0)
        dispatcher.wake()
        await spin_until(lambda: len(service.sync_once_calls) == 2,
                         what="deferred round retried")
        assert await dispatcher.drain(2.0) is True
        assert service.sync_once_calls == [key, key]
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_deferred_window_exhausts_without_hot_loop():
    """A key that never stops being DEFERRED gets a bounded number of
    rounds inside its 15s budget, then lands back in the repair queue's
    hands (entry dropped, no spin)."""
    service = ScriptedService()
    service.sync_once_outcomes.extend([SyncOutcome.DEFERRED] * 64)
    clock = FakeClock()
    dispatcher = make_dispatcher(
        service, Mock(), policy=DispatcherPolicy(worker_count=1), clock=clock
    )
    assert (
        dispatcher.request_sync(bot_pk=7, env="dev", avernet_tenant="a",
                               reason=SyncReason.REPAIR)
        is SubmissionResult.QUEUED
    )
    await dispatcher.attach()
    try:
        # First round runs immediately at t=1000; then the dispatcher
        # re-runs it on the 1s/2s/4s/8s backoff inside its 15s budget.
        await spin_until(lambda: len(service.sync_once_calls) == 1,
                         what="first (deferred) round")
        for step, expected_rounds in ((1.0, 2), (2.0, 3), (4.0, 4), (8.0, 5)):
            clock.advance(step)
            dispatcher.wake()
            await spin_until(
                lambda e=expected_rounds: len(service.sync_once_calls) == e,
                what=f"deferred round {expected_rounds}",
            )
        # The fifth round runs at the window's edge and is dropped at its
        # completion — the wait-scheduler must not spin a sixth time.
        await spin_until(lambda: dispatcher.stats.entries == 0,
                         what="deferred entry dropped after its window")
        assert len(service.sync_once_calls) == 5  # bounded, no hot loop
    finally:
        await dispatcher.stop()


# -- 9. close semantics -------------------------------------------------------------


@pytest.mark.asyncio
async def test_closed_admission_rejects_but_drains_what_it_holds():
    service = ScriptedService()
    dispatcher = make_dispatcher(
        service, Mock(), policy=DispatcherPolicy(worker_count=1)
    )
    assert (
        dispatcher.request_sync(bot_pk=1, env="dev", avernet_tenant="a",
                               reason=SyncReason.REPAIR)
        is SubmissionResult.QUEUED
    )
    dispatcher.close_admission()
    assert (
        dispatcher.request_sync(bot_pk=2, env="dev", avernet_tenant="a",
                               reason=SyncReason.REPAIR)
        is SubmissionResult.REJECTED
    )
    await dispatcher.attach()
    try:
        assert await dispatcher.drain(2.0) is True
        assert service.sync_once_calls == [SyncKey("a", "dev", 1)]
    finally:
        await dispatcher.stop()


def test_stop_is_idempotent_without_a_loop():
    dispatcher = make_dispatcher(Mock(), Mock())
    assert dispatcher.stats.attached is False
    # Not attached: stop is a clean no-op, admission still works afterwards.
    dispatcher.close_admission()
    assert (
        dispatcher.request_sync(bot_pk=1, env="dev", avernet_tenant="a",
                               reason=SyncReason.REPAIR)
        is SubmissionResult.REJECTED
    )


# -- drain vs in-flight space-hint expansion ----------------------------------


@pytest.mark.asyncio
async def test_drain_holds_until_a_popped_hint_finishes_its_expansion():
    """Drain must not answer "done" over the hint-pop admission window.

    A worker between popping a space hint and admitting its per-Bot
    keys (parked on a gate inside the page read here) leaves both the
    entries and the pending-hint counters reading zero. ``drain`` must
    treat the popped hint as in-flight work and keep waiting — a
    maintenance/shutdown wait that returned over this window would
    silently drop queued space-fan-out work.
    """
    service = ScriptedService()
    service.gate.set()
    entered = threading.Event()
    release = threading.Event()

    def _gated_page(change, *, after_pk, limit):
        entered.set()
        release.wait(timeout=10)
        return ()

    reader = Mock()
    reader.page_space_keys.side_effect = _gated_page
    dispatcher = make_dispatcher(service, reader)
    await dispatcher.attach()
    try:
        assert (
            dispatcher.request_space_sync(SpaceChange("a", "dev", ("301",), ()))
            is SubmissionResult.QUEUED
        )
        await asyncio.to_thread(entered.wait, 10)
        assert dispatcher.stats.entries == 0  # nothing admitted yet

        drained = asyncio.create_task(dispatcher.drain(5))
        # Let the drain task run several poll cycles while the
        # expansion is parked — pacing, not a timestamp verdict.
        await asyncio.sleep(0.1)
        assert not drained.done(), (
            "drain answered done while a popped hint was still being"
            " expanded into per-Bot keys"
        )

        release.set()
        assert await drained is True
    finally:
        release.set()
        await dispatcher.stop()
