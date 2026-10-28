"""Unit tests for the bot manager sync service (single round + retry).

Task 5 pins the orchestration: claim → read facts → project → begin
attempt → PUT with remaining budget → verify total → finish attempt →
release lease, with a bounded retry loop that never replays an
uncertain write.

Safety rules driven here (each rule has at least one test):

- ``begin``-uncertain or UNKNOWN delivery ⇒ never send / never finish;
  the in-flight marker stays so the next ``sync_once`` is BLOCKED.
- After the PUT was invoked, ANY cancellation keeps the marker and
  reports UNKNOWN.
- Cancelled ``to_thread`` reads cannot stop the underlying SQL — a late
  result is dropped before ``begin``/BCS (fake clock + thread Event).
- claim BUSY is the only DEFERRED; every other infrastructure
  ambiguity is FAILED, never BUSY.
- NOT_SENT is the only retryable failure: finish first, then re-read
  and send again with a NEW attempt id, inside the remaining budget.
- Lease renewal only when the lease window is smaller than the
  remaining budget + 2s, strictly inside the deadline; default 15/30s
  policy does zero renewals.
- No test waits real seconds: waits are recorded fakes, clocks are
  injected, and blocking SQL is released by threading Events.
"""
from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_collaborator.manager_sync.diagnostics import (
    SyncDiagnostics,
)
from agentclaw.community.core.bot_collaborator.manager_sync.retry import SyncRunPolicy
from agentclaw.community.core.bot_collaborator.manager_sync.service import (
    BotManagerSyncService,
)
from agentclaw.community.kernel.bot_manager_sync import (
    CollaboratorFact,
    DeliveryFailure,
    ManagerSetChange,
    SnapshotUnavailable,
    SyncFacts,
    SyncKey,
    SyncOutcome,
)
from agentclaw.community.plugin_api.bcs_managers import BcsManagersError
from agentclaw.community.plugin_api.manager_sync_coordination import (
    CoordinationLost,
    CoordinationUnavailable,
)
from agentclaw.community.plugins.local.bcs_managers import (
    RecordingBcsManagersPlugin,
)
from agentclaw.community.plugins.local.manager_sync_coordination import (
    MemoryManagerSyncCoordinator,
)


# -- shared fixtures / helpers ----------------------------------------------


def make_service(facts, outcomes):
    """The fixture pinned by the task brief: Mock reader returning
    ``facts``, recording BCS fake pre-filled with ``outcomes``, and the
    in-memory coordinator. Returns ``(service, bcs, coordinator)``."""
    reader = Mock()
    reader.read_facts.return_value = facts
    bcs = RecordingBcsManagersPlugin()
    bcs.outcomes.extend(outcomes)
    coordinator = MemoryManagerSyncCoordinator()
    service = BotManagerSyncService(reader=reader, bcs=bcs, coordinator=coordinator)
    return service, bcs, coordinator


def _facts(collaborators=("admin",)) -> SyncFacts:
    rows = tuple(
        CollaboratorFact(user_id=uid, role="admin", space_eligible=True)
        for uid in collaborators
    )
    return SyncFacts(SyncKey("a", "dev", 7), "bot", "entity", "owner", rows)


class FakeClock:
    """Frozen monotonic clock the tests advance explicitly."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def _identity_jitter(base: float) -> float:
    return base


def _wait_recorder(clock: FakeClock | None = None):
    """Injected async wait: records the delay, never sleeps; optionally
    advances the fake clock by the waited amount."""
    waits: list[float] = []

    async def wait(seconds: float) -> None:
        waits.append(seconds)
        if clock is not None:
            clock.advance(seconds)

    return wait, waits


def _build(
    *,
    facts: SyncFacts | None = None,
    outcomes=(),
    policy: SyncRunPolicy | None = None,
    clock=None,
    wait=None,
    jitter=_identity_jitter,
    coordinator: MemoryManagerSyncCoordinator | None = None,
    reader=None,
    diag: SyncDiagnostics | None = None,
):
    """Service builder for the injected tests (see make_service first)."""
    if reader is None:
        reader = Mock()
        reader.read_facts.return_value = facts
    bcs = RecordingBcsManagersPlugin()
    bcs.outcomes.extend(outcomes)
    coord = coordinator if coordinator is not None else (
        MemoryManagerSyncCoordinator(clock=clock)
    )
    diagnostics = diag if diag is not None else SyncDiagnostics()
    service = BotManagerSyncService(
        reader=reader,
        bcs=bcs,
        coordinator=coord,
        policy=policy,
        clock=clock,
        jitter=jitter,
        wait=wait,
        diagnostics=diagnostics,
    )
    return service, bcs, coord, diagnostics


def _blocking_reader(facts: SyncFacts, gate: threading.Event, entered: threading.Event):
    """A reader whose SQL is stuck in a thread until the gate opens."""

    def read_facts(key: SyncKey) -> SyncFacts:
        entered.set()
        assert gate.wait(timeout=30), "test gate never opened"
        return facts

    return SimpleNamespace(read_facts=read_facts)


async def _until(predicate, timeout: float = 10.0) -> None:
    """Await a (thread-side) condition without real long sleeps."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("test condition not reached in time")
        await asyncio.sleep(0.01)


def _err(kind: DeliveryFailure, code: str) -> BcsManagersError:
    return BcsManagersError(kind, code)


# -- the brief's verbatim test ----------------------------------------------


@pytest.mark.asyncio
async def test_unknown_blocks_next_snapshot():
    facts = SyncFacts(SyncKey("a", "dev", 7), "bot", "entity", "owner", ())
    service, bcs, coordinator = make_service(
        facts, [BcsManagersError(DeliveryFailure.UNKNOWN, "read_timeout")]
    )
    assert await service.sync_once(facts.key) is SyncOutcome.UNKNOWN
    assert await service.sync_once(facts.key) is SyncOutcome.BLOCKED
    assert len(bcs.calls) == 1
    assert coordinator.read_blocked(facts.key) is not None


# -- happy paths -------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirmed_delivery_and_zero_renewals_under_default_policy():
    facts = _facts(("admin", "member"))
    service, bcs, coordinator, diag = _build(facts=facts)
    assert await service.sync_once(facts.key) is SyncOutcome.CONFIRMED
    assert len(bcs.calls) == 1
    assert coordinator.read_blocked(facts.key) is None
    # 15s budget vs 30s lease: the lease always covers the round.
    assert coordinator.calls_to("renew") == []
    # the lease is released, so a second round claims fresh and works
    assert await service.sync_once(facts.key) is SyncOutcome.CONFIRMED
    assert len(bcs.calls) == 2
    assert diag.counted.get(("send_confirmed", "confirmed")) == 2


@pytest.mark.asyncio
async def test_empty_roster_acknowledged_empty_via_real_diff():
    facts = _facts(collaborators=())
    service, bcs, coordinator, diag = _build(facts=facts)
    assert await service.sync_once(facts.key) is SyncOutcome.ACKNOWLEDGED_EMPTY
    assert bcs.calls[0].manager_user_ids == ()
    assert coordinator.read_blocked(facts.key) is None
    assert diag.counted.get(("send_acknowledged_empty", "acknowledged_empty")) == 1


@pytest.mark.asyncio
async def test_empty_roster_all_zero_scripted_reply_is_acknowledged_empty():
    facts = _facts(collaborators=())
    service, _, _, diag = _build(
        facts=facts, outcomes=[ManagerSetChange(0, 0, 0, "req-empty")]
    )
    assert await service.sync_once(facts.key) is SyncOutcome.ACKNOWLEDGED_EMPTY
    assert diag.counted.get(("send_acknowledged_empty", "acknowledged_empty")) == 1


@pytest.mark.asyncio
async def test_nonempty_roster_all_zero_reply_is_drift():
    """False success: BCS acknowledges zero against a non-empty push."""
    facts = _facts(("admin", "member"))
    service, bcs, _, diag = _build(
        facts=facts, outcomes=[ManagerSetChange(0, 0, 0, "req-zero")]
    )
    assert await service.sync_once(facts.key) is SyncOutcome.DRIFT
    assert len(bcs.calls) == 1  # drift is terminal — never retried
    assert diag.counted.get(("send_drift", "drift")) == 1


def test_holder_is_process_unique_per_service_instance():
    """Two instances in one process must never share a holder string —
    the lease value IS the holder, and a shared one lets two workers
    renew/release each other's lease (cross-task contract)."""
    facts = _facts()
    coordinator = MemoryManagerSyncCoordinator()

    def _new_service() -> BotManagerSyncService:
        reader = Mock()
        reader.read_facts.return_value = facts
        return BotManagerSyncService(
            reader=reader, bcs=RecordingBcsManagersPlugin(), coordinator=coordinator
        )

    first = _new_service()
    second = _new_service()
    prefix = f"{socket.gethostname()}:{os.getpid()}:"
    assert first._holder != second._holder
    assert first._holder.startswith(prefix)
    assert second._holder.startswith(prefix)
    assert len(first._holder.rsplit(":", 1)[1]) == 8


# -- delivery failures: UNKNOWN / REJECTED / NOT_SENT -------------------------


@pytest.mark.asyncio
async def test_unknown_delivery_keeps_marker_and_releases_lease():
    facts = _facts(("admin",))
    service, bcs, coordinator, diag = _build(
        facts=facts, outcomes=[_err(DeliveryFailure.UNKNOWN, "upstream_5xx")]
    )
    assert await service.sync_once(facts.key) is SyncOutcome.UNKNOWN
    assert len(bcs.calls) == 1
    # never finish an uncertain attempt — the marker outlives the lease
    assert coordinator.calls_to("finish") == []
    assert coordinator.read_blocked(facts.key) is not None
    # the lease itself is released (markers are never touched)
    assert len(coordinator.calls_to("release")) == 1
    assert diag.counted.get(("send_unknown", "unknown")) == 1


@pytest.mark.asyncio
async def test_rejected_is_terminal_failed_round():
    facts = _facts(("admin",))
    service, bcs, coordinator, diag = _build(
        facts=facts, outcomes=[_err(DeliveryFailure.REJECTED, "capacity")]
    )
    assert await service.sync_once(facts.key) is SyncOutcome.FAILED
    assert len(bcs.calls) == 1  # REJECTED never retried within the round
    assert coordinator.read_blocked(facts.key) is None  # marker finished
    assert diag.counted.get(("send_rejected", "failed")) == 1


@pytest.mark.asyncio
async def test_not_sent_twice_then_success_with_new_attempt_ids():
    facts = _facts(("admin",))
    clock = FakeClock()
    wait, waits = _wait_recorder()
    service, bcs, coordinator, diag = _build(
        facts=facts,
        outcomes=[
            _err(DeliveryFailure.NOT_SENT, "conn_reset"),
            _err(DeliveryFailure.NOT_SENT, "conn_refused"),
            ManagerSetChange(1, 0, 1, "req-3"),
        ],
        clock=clock,
        wait=wait,
    )
    assert await service.sync_once(facts.key) is SyncOutcome.CONFIRMED
    assert len(bcs.calls) == 3
    # exponential backoff evidence: ~1s then ~2s (+jitter=identity)
    assert waits == [1.0, 2.0]
    # every send used a fresh attempt id over the same digest
    begins = coordinator.calls_to("begin")
    assert len(begins) == 3
    assert len({b.kwargs["attempt_id"] for b in begins}) == 3
    assert len({b.kwargs["digest"] for b in begins}) == 1
    # each NOT_SENT was finished before the next re-read
    assert len(coordinator.calls_to("finish")) == 3
    assert coordinator.read_blocked(facts.key) is None
    assert diag.counted.get(("send_confirmed", "confirmed")) == 1


@pytest.mark.asyncio
async def test_not_sent_exhausts_max_attempts_then_fails():
    facts = _facts(("admin",))
    clock = FakeClock()
    wait, waits = _wait_recorder()
    service, bcs, coordinator, diag = _build(
        facts=facts,
        outcomes=[
            _err(DeliveryFailure.NOT_SENT, "reset"),
            _err(DeliveryFailure.NOT_SENT, "refused"),
            _err(DeliveryFailure.NOT_SENT, "timeout"),
        ],
        clock=clock,
        wait=wait,
    )
    assert await service.sync_once(facts.key) is SyncOutcome.FAILED
    assert len(bcs.calls) == 3  # attempt cap: never a 4th send
    assert waits == [1.0, 2.0]
    assert coordinator.read_blocked(facts.key) is None
    assert diag.counted.get(("retries_exhausted", "failed")) == 1


@pytest.mark.asyncio
async def test_backoff_is_bounded_by_remaining_budget():
    """Budget 2.5s: waits run 1.0s then only 1.5s (base 2 capped)."""
    facts = _facts(("admin",))
    clock = FakeClock()
    wait, waits = _wait_recorder(clock=clock)
    service, bcs, _, diag = _build(
        facts=facts,
        outcomes=[
            _err(DeliveryFailure.NOT_SENT, "reset"),
            _err(DeliveryFailure.NOT_SENT, "refused"),
            _err(DeliveryFailure.NOT_SENT, "refused"),
        ],
        policy=SyncRunPolicy(max_attempts=3, budget_seconds=2.5, lease_seconds=30),
        clock=clock,
        wait=wait,
    )
    assert await service.sync_once(facts.key) is SyncOutcome.FAILED
    assert waits == [1.0, 1.5]  # 1s; then base 2s capped at 1.5s remaining
    assert len(bcs.calls) == 2  # third attempt found no budget left
    assert diag.counted.get(("read_late", "failed")) == 1


@pytest.mark.asyncio
async def test_put_contract_violation_is_unknown_with_marker_kept():
    """A non-BcsManagersError escape from the plugin is an uncertain
    delivery exactly like an UNKNOWN classification."""
    facts = _facts(("admin",))
    reader = Mock()
    reader.read_facts.return_value = facts
    bcs = _ExplodingBcs()
    coordinator = MemoryManagerSyncCoordinator()
    service = BotManagerSyncService(reader=reader, bcs=bcs, coordinator=coordinator)
    assert await service.sync_once(facts.key) is SyncOutcome.UNKNOWN
    assert len(bcs.calls) == 1
    assert coordinator.calls_to("finish") == []
    assert coordinator.read_blocked(facts.key) is not None


# -- claim: BUSY / BLOCKED / infra -------------------------------------------


@pytest.mark.asyncio
async def test_claim_busy_returns_deferred_and_sends_nothing():
    facts = _facts(("admin",))
    coordinator = MemoryManagerSyncCoordinator()
    seeded = coordinator.claim(
        facts.key, holder="another-deployment", ttl_seconds=30
    )
    assert seeded.lease is not None
    service, bcs, coordinator, diag = _build(
        facts=facts, coordinator=coordinator
    )
    assert await service.sync_once(facts.key) is SyncOutcome.DEFERRED
    assert bcs.calls == []
    assert coordinator.calls_to("begin") == []
    assert coordinator.calls_to("release") == []  # lease is not ours
    assert diag.counted.get(("claim_busy", "deferred")) == 1


@pytest.mark.asyncio
async def test_claim_infra_failure_is_failed_never_busy():
    facts = _facts(("admin",))
    coordinator = MemoryManagerSyncCoordinator(generation=None)
    service, bcs, _, diag = _build(facts=facts, coordinator=coordinator)
    assert await service.sync_once(facts.key) is SyncOutcome.FAILED
    assert bcs.calls == []
    assert diag.counted.get(("claim_failed", "failed")) == 1


# -- owner hold ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_hold_owner_check_sets_marker_and_blocks_sync():
    """hold_owner_check never sends HTTP: it only plants the marker and
    reports BLOCKED; the next sync_once is stopped at claim time."""
    facts = _facts(("admin",))
    service, bcs, coordinator, diag = _build(facts=facts)
    assert await service.hold_owner_check(facts.key) is SyncOutcome.BLOCKED
    assert coordinator.read_blocked(facts.key) is not None
    assert await service.sync_once(facts.key) is SyncOutcome.BLOCKED
    assert bcs.calls == []
    assert coordinator.calls_to("begin") == []
    assert diag.counted.get(("hold_set", "blocked")) == 1


@pytest.mark.asyncio
async def test_hold_owner_check_infra_failure_alerts_and_fails():
    facts = _facts(("admin",))
    coordinator = MemoryManagerSyncCoordinator(generation=None)
    service, bcs, _, diag = _build(facts=facts, coordinator=coordinator)
    assert await service.hold_owner_check(facts.key) is SyncOutcome.FAILED
    assert bcs.calls == []
    assert diag.counted.get(("hold_failed", "failed")) == 1


# -- read / projection failures ------------------------------------------------


@pytest.mark.asyncio
async def test_read_failure_sends_nothing_and_releases_lease():
    facts = _facts(("admin",))

    def boom(key: SyncKey) -> SyncFacts:
        raise SnapshotUnavailable("not_found")

    service, bcs, coordinator, diag = _build(
        facts=facts, reader=SimpleNamespace(read_facts=boom)
    )
    assert await service.sync_once(facts.key) is SyncOutcome.FAILED
    assert bcs.calls == []
    assert coordinator.calls_to("begin") == []
    assert len(coordinator.calls_to("release")) == 1
    assert diag.counted.get(("read_failed", "failed")) == 1
    # the released lease lets the next round claim and succeed
    reader = Mock()
    reader.read_facts.return_value = facts
    service2, _, _, _ = _build(facts=facts, coordinator=coordinator, reader=reader)
    assert await service2.sync_once(facts.key) is SyncOutcome.CONFIRMED


@pytest.mark.asyncio
async def test_unprojectable_facts_fail_without_send():
    bad = SyncFacts(
        SyncKey("a", "dev", 7), "bot", "entity", "owner",
        (CollaboratorFact(user_id="guest-1", role="guest", space_eligible=True),),
    )
    service, bcs, coordinator, diag = _build(facts=bad)
    assert await service.sync_once(bad.key) is SyncOutcome.FAILED
    assert bcs.calls == []
    assert coordinator.calls_to("begin") == []
    assert diag.counted.get(("projection_failed", "failed")) == 1


class _HangingBcs(RecordingBcsManagersPlugin):
    """BCS fake whose PUT parks on an asyncio Event (never returns)."""

    def __init__(self, entered: asyncio.Event, hang_gate: asyncio.Event) -> None:
        super().__init__()
        self._entered = entered
        self._hang_gate = hang_gate

    async def replace_managers(self, snapshot, *, timeout_seconds):
        self.calls.append(snapshot)
        self._entered.set()
        await self._hang_gate.wait()
        raise AssertionError("unreachable: cancelled before the gate")


class _ExplodingBcs(RecordingBcsManagersPlugin):
    """BCS fake that violates the contract with a raw RuntimeError."""

    async def replace_managers(self, snapshot, *, timeout_seconds):
        self.calls.append(snapshot)
        raise RuntimeError("socket exploded mid-write")


# -- late reads: deadline & cancellation ----------------------------------------


@pytest.mark.asyncio
async def test_late_read_after_deadline_never_sends():
    """A threaded SQL read cannot be stopped; its late result must not
    leak past the deadline into begin/BCS (fake clock + thread Event)."""
    facts = _facts(("admin",))
    clock = FakeClock()
    coordinator = MemoryManagerSyncCoordinator(clock=clock)
    gate, entered = threading.Event(), threading.Event()
    service, bcs, coordinator, _ = _build(
        facts=facts,
        reader=_blocking_reader(facts, gate, entered),
        policy=SyncRunPolicy(max_attempts=1, budget_seconds=15, lease_seconds=30),
        clock=clock,
        coordinator=coordinator,
    )
    task = asyncio.create_task(service.sync_once(facts.key))
    await _until(entered.is_set)
    clock.advance(16)  # the SQL result now arrives past the deadline
    gate.set()
    assert await asyncio.wait_for(task, 10) is SyncOutcome.FAILED
    assert bcs.calls == []
    assert coordinator.calls_to("begin") == []
    assert len(coordinator.calls_to("release")) == 1  # lease still freed


@pytest.mark.asyncio
async def test_cancelled_read_never_sends():
    """Cancelling the awaiting coroutine while the SQL is stuck must
    leave no marker and issue no begin/BCS call."""
    facts = _facts(("admin",))
    clock = FakeClock()
    coordinator = MemoryManagerSyncCoordinator(clock=clock)
    gate, entered = threading.Event(), threading.Event()
    service, bcs, coordinator, _ = _build(
        facts=facts,
        reader=_blocking_reader(facts, gate, entered),
        policy=SyncRunPolicy(max_attempts=1, budget_seconds=15, lease_seconds=30),
        clock=clock,
        coordinator=coordinator,
    )
    task = asyncio.create_task(service.sync_once(facts.key))
    await _until(entered.is_set)
    task.cancel()
    gate.set()  # release the SQL anyway — the result must be dropped
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert bcs.calls == []
    assert coordinator.calls_to("begin") == []
    assert coordinator.read_blocked(facts.key) is None
    assert len(coordinator.calls_to("release")) == 1


# -- cancellation of a dispatched send -------------------------------------------


@pytest.mark.asyncio
async def test_cancel_during_put_keeps_unknown_marker():
    """Cancellation AFTER the PUT was invoked is an unknown delivery:
    no finish call, marker kept, UNKNOWN returned (not re-raised)."""
    facts = _facts(("admin",))
    reader = Mock()
    reader.read_facts.return_value = facts
    entered = asyncio.Event()
    hang_gate = asyncio.Event()
    bcs = _HangingBcs(entered, hang_gate)
    coordinator = MemoryManagerSyncCoordinator()
    diag = SyncDiagnostics()
    service = BotManagerSyncService(
        reader=reader, bcs=bcs, coordinator=coordinator, diagnostics=diag
    )
    task = asyncio.create_task(service.sync_once(facts.key))
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    assert await task is SyncOutcome.UNKNOWN  # swallowed on purpose
    assert len(bcs.calls) == 1  # the invoke is recorded
    assert coordinator.calls_to("finish") == []  # marker never finished
    assert coordinator.read_blocked(facts.key) is not None
    assert diag.counted.get(("send_cancelled", "unknown")) == 1


# -- begin: uncertain / lost -------------------------------------------------------


@pytest.mark.asyncio
async def test_begin_uncertain_never_sends_unknown_without_finish():
    facts = _facts(("admin",))

    def begin_uncertain(lease, *, attempt_id, digest):
        raise CoordinationUnavailable("eval timeout")

    service, bcs, coordinator, diag = _build(facts=facts)
    coordinator.set_override("begin", begin_uncertain)
    assert await service.sync_once(facts.key) is SyncOutcome.UNKNOWN
    assert bcs.calls == []
    assert coordinator.calls_to("finish") == []
    assert diag.counted.get(("begin_uncertain", "unknown")) == 1


@pytest.mark.asyncio
async def test_begin_coordination_lost_blocks_without_send():
    facts = _facts(("admin",))

    def begin_lost(lease, *, attempt_id, digest):
        raise CoordinationLost("marker present")

    service, bcs, coordinator, diag = _build(facts=facts)
    coordinator.set_override("begin", begin_lost)
    assert await service.sync_once(facts.key) is SyncOutcome.BLOCKED
    assert bcs.calls == []
    assert coordinator.calls_to("finish") == []
    assert diag.counted.get(("begin_lost", "blocked")) == 1


# -- finish: mismatch / infra --------------------------------------------------------


@pytest.mark.asyncio
async def test_finish_mismatch_after_confirmed_send_blocks():
    facts = _facts(("admin",))
    service, bcs, coordinator, diag = _build(facts=facts)
    coordinator.set_override("finish", lambda attempt: False)
    assert await service.sync_once(facts.key) is SyncOutcome.BLOCKED
    assert len(bcs.calls) == 1
    assert coordinator.read_blocked(facts.key) is not None  # marker kept
    assert diag.counted.get(("finish_mismatch", "blocked")) == 1


@pytest.mark.asyncio
async def test_finish_infra_failure_after_confirmed_send_blocks():
    facts = _facts(("admin",))

    def finish_unavailable(attempt):
        raise CoordinationUnavailable("redis down")

    service, bcs, coordinator, diag = _build(facts=facts)
    coordinator.set_override("finish", finish_unavailable)
    assert await service.sync_once(facts.key) is SyncOutcome.BLOCKED
    assert len(bcs.calls) == 1
    assert coordinator.read_blocked(facts.key) is not None
    assert diag.counted.get(("finish_failed", "blocked")) == 1


# -- lease renewal -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_short_lease_renews_then_confirms():
    """With a 3s lease under a 15s budget the send must renew first."""
    facts = _facts(("admin",))
    service, bcs, coordinator, _ = _build(
        facts=facts,
        policy=SyncRunPolicy(max_attempts=1, budget_seconds=15, lease_seconds=3),
    )
    assert await service.sync_once(facts.key) is SyncOutcome.CONFIRMED
    assert len(coordinator.calls_to("renew")) == 1
    assert len(bcs.calls) == 1


@pytest.mark.asyncio
async def test_short_lease_failed_renewal_sends_nothing():
    facts = _facts(("admin",))
    coordinator = MemoryManagerSyncCoordinator()

    def renew_refused(lease, *, ttl_seconds):
        return False

    coordinator.set_override("renew", renew_refused)
    service, bcs, coordinator, diag = _build(
        facts=facts,
        policy=SyncRunPolicy(max_attempts=3, budget_seconds=15, lease_seconds=3),
        coordinator=coordinator,
    )
    assert await service.sync_once(facts.key) is SyncOutcome.FAILED
    assert bcs.calls == []
    assert coordinator.calls_to("begin") == []
    assert len(coordinator.calls_to("renew")) == 1
    assert diag.counted.get(("lease_renew_failed", "failed")) == 1


@pytest.mark.asyncio
async def test_renew_infra_failure_sends_nothing():
    facts = _facts(("admin",))
    coordinator = MemoryManagerSyncCoordinator()

    def renew_down(lease, *, ttl_seconds):
        raise CoordinationUnavailable("generation lost")

    coordinator.set_override("renew", renew_down)
    service, bcs, coordinator, diag = _build(
        facts=facts,
        policy=SyncRunPolicy(max_attempts=1, budget_seconds=15, lease_seconds=3),
        coordinator=coordinator,
    )
    assert await service.sync_once(facts.key) is SyncOutcome.FAILED
    assert bcs.calls == []
    assert diag.counted.get(("lease_renew_failed", "failed")) == 1