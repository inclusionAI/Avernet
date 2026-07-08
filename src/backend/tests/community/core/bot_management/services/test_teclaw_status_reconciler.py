"""Tests for TeclawStatusReconciler — post-approve teclaw status polling.

Timing/threading are driven by fakes: a ``FakeClock`` is the deadline source and
a ``FakeScheduler`` records each ``schedule(fn, delay)`` and replays the fns FIFO
(advancing the clock by each delay), so the self-rescheduling poll loop runs
synchronously with no real threads and no real ≤10-minute waits. The real
``_DelayedScheduler`` is exercised separately with tiny delays.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.teclaw_status_reconciler import (
    TeclawStatusReconciler,
    map_publish_status,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError


class FakeClock:
    """Deterministic monotonic clock advanced explicitly (by the scheduler)."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t


class FakeScheduler:
    """Records scheduled callables and replays them FIFO, advancing the clock by
    each task's delay just before it runs — simulating the real scheduler firing.
    """

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.calls: list[tuple] = []  # (fn, delay) in scheduling order
        self._pending: list[tuple] = []

    def schedule(self, fn, delay_s: float) -> None:
        self.calls.append((fn, delay_s))
        self._pending.append((fn, delay_s))

    def drive(self, max_ticks: int = 200) -> int:
        """Run pending fns until none remain (a step may reschedule itself)."""
        ticks = 0
        while self._pending and ticks < max_ticks:
            fn, delay = self._pending.pop(0)
            self._clock.t += delay
            fn()
            ticks += 1
        assert ticks < max_ticks, "poll loop did not terminate"
        return ticks


def _make(*, poll_interval_s: float = 10.0, timeout_s: float = 600.0):
    baas = MagicMock()
    bot_repo = MagicMock()
    binding_repo = MagicMock()
    clock = FakeClock()
    scheduler = FakeScheduler(clock)
    svc = TeclawStatusReconciler(
        baas_service=baas,
        bot_repository=bot_repo,
        device_binding_repo=binding_repo,
        poll_interval_s=poll_interval_s,
        timeout_s=timeout_s,
        schedule=scheduler.schedule,
        clock=clock.now,
    )
    return svc, baas, bot_repo, binding_repo, clock, scheduler


# ── map_publish_status ────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "publish_status,expected",
    [
        ("SUCCESS", "ACTIVE"),
        ("success", "ACTIVE"),
        ("FAILED", "FAILED"),
        ("REJECTED", "FAILED"),
        ("REVOKED", "FAILED"),
        ("PENDING", "PENDING"),
        ("ACTIVE", "PENDING"),  # publish "executing" — not done yet
        ("APPROVING", "PENDING"),
        ("INIT", "PENDING"),
        ("", "PENDING"),
        (None, "PENDING"),
    ],
)
def test_map_publish_status(publish_status, expected) -> None:
    assert map_publish_status(publish_status) == expected


# ── reconcile_once ────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "baas_status,expected",
    [("SUCCESS", "ACTIVE"), ("FAILED", "FAILED"), ("PENDING", "PENDING"), ("INIT", "PENDING")],
)
def test_reconcile_once_maps_progress(baas_status, expected) -> None:
    svc, baas, *_ = _make()
    baas.get_publish_progress.return_value = {"status": baas_status}

    assert svc.reconcile_once(9) == expected
    baas.get_publish_progress.assert_called_once_with(9)


@pytest.mark.unit
def test_reconcile_once_none_on_baas_error() -> None:
    svc, baas, *_ = _make()
    baas.get_publish_progress.side_effect = BaasServiceError("boom")

    assert svc.reconcile_once(9) is None


@pytest.mark.unit
def test_reconcile_once_pending_when_progress_empty() -> None:
    svc, baas, *_ = _make()
    baas.get_publish_progress.return_value = None

    assert svc.reconcile_once(9) == "PENDING"


# ── start: schedules the first poll step ──────────────────────────────────────


@pytest.mark.unit
def test_start_schedules_first_step_immediately_with_ids() -> None:
    svc, _baas, _bot, _binding, _clock, scheduler = _make(timeout_s=600.0)

    svc.start(publish_id=9, bot_id="b1", owner_id="u1", binding_id=77)

    assert len(scheduler.calls) == 1
    fn, delay = scheduler.calls[0]
    assert delay == 0.0
    # functools.partial(self._poll_step, publish_id, bot_id, owner_id,
    #                    binding_id, deadline, attempt)
    assert fn.func == svc._poll_step
    assert fn.args == (9, "b1", "u1", 77, 600.0, 1)  # deadline = clock(0) + timeout


@pytest.mark.unit
@pytest.mark.parametrize("publish_id,binding_id", [(None, 77), (9, 0), (9, None)])
def test_start_noop_when_ids_missing(publish_id, binding_id) -> None:
    svc, _baas, _bot, _binding, _clock, scheduler = _make()

    svc.start(publish_id=publish_id, bot_id="b1", owner_id="u1", binding_id=binding_id)

    assert scheduler.calls == []


# ── full loop: persist on terminal & stop ─────────────────────────────────────


@pytest.mark.unit
def test_converges_to_active_after_pending_polls_and_stops() -> None:
    svc, baas, bot_repo, binding_repo, clock, scheduler = _make()
    baas.get_publish_progress.side_effect = [
        {"status": "PENDING"},
        {"status": "PENDING"},
        {"status": "SUCCESS"},
    ]

    svc.start(publish_id=9, bot_id="b1", owner_id="u1", binding_id=77)
    scheduler.drive()

    bot_repo.update_by_owner.assert_called_once_with("b1", "u1", {"status": "ACTIVE"})
    binding_repo.update_status.assert_called_once_with(binding_id=77, status="ACTIVE")
    assert baas.get_publish_progress.call_count == 3
    assert len(scheduler.calls) == 3  # initial + 2 reschedules, none after terminal
    assert clock.t == 20.0  # two 10s reschedule delays


@pytest.mark.unit
def test_persists_failed_on_first_poll() -> None:
    svc, baas, bot_repo, binding_repo, clock, scheduler = _make()
    baas.get_publish_progress.return_value = {"status": "FAILED"}

    svc.start(publish_id=9, bot_id="b1", owner_id="u1", binding_id=77)
    scheduler.drive()

    bot_repo.update_by_owner.assert_called_once_with("b1", "u1", {"status": "FAILED"})
    binding_repo.update_status.assert_called_once_with(binding_id=77, status="FAILED")
    assert len(scheduler.calls) == 1  # terminal immediately — no reschedule
    assert clock.t == 0.0


# ── give up at timeout leaving PENDING ────────────────────────────────────────


@pytest.mark.unit
def test_gives_up_at_timeout_without_persisting() -> None:
    svc, baas, bot_repo, binding_repo, clock, scheduler = _make(
        poll_interval_s=10.0, timeout_s=25.0
    )
    baas.get_publish_progress.return_value = {"status": "PENDING"}

    svc.start(publish_id=9, bot_id="b1", owner_id="u1", binding_id=77)
    scheduler.drive()

    bot_repo.update_by_owner.assert_not_called()
    binding_repo.update_status.assert_not_called()
    # polls at t=0,10,20 reschedule; at t=30 (>=25) it gives up.
    assert baas.get_publish_progress.call_count == 4
    assert clock.t == 30.0


# ── best-effort on transient BaaS errors ──────────────────────────────────────


@pytest.mark.unit
def test_tolerates_transient_error_then_converges() -> None:
    svc, baas, bot_repo, binding_repo, _clock, scheduler = _make()
    baas.get_publish_progress.side_effect = [
        BaasServiceError("transient"),
        {"status": "PENDING"},
        {"status": "SUCCESS"},
    ]

    svc.start(publish_id=9, bot_id="b1", owner_id="u1", binding_id=77)
    scheduler.drive()

    bot_repo.update_by_owner.assert_called_once_with("b1", "u1", {"status": "ACTIVE"})
    binding_repo.update_status.assert_called_once_with(binding_id=77, status="ACTIVE")
    assert baas.get_publish_progress.call_count == 3


@pytest.mark.unit
def test_persistent_error_gives_up_without_raising_or_writing() -> None:
    svc, baas, bot_repo, binding_repo, _clock, scheduler = _make(
        poll_interval_s=10.0, timeout_s=15.0
    )
    baas.get_publish_progress.side_effect = BaasServiceError("always down")

    svc.start(publish_id=9, bot_id="b1", owner_id="u1", binding_id=77)
    scheduler.drive()  # must terminate; never raises out of a step

    bot_repo.update_by_owner.assert_not_called()
    binding_repo.update_status.assert_not_called()


# ── best-effort on transient persist failures ─────────────────────────────────


@pytest.mark.unit
def test_retries_when_persist_fails_then_succeeds() -> None:
    svc, baas, bot_repo, binding_repo, _clock, scheduler = _make()
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}
    bot_repo.update_by_owner.side_effect = [RuntimeError("db down"), {"ok": True}]

    svc.start(publish_id=9, bot_id="b1", owner_id="u1", binding_id=77)
    scheduler.drive()

    assert bot_repo.update_by_owner.call_count == 2
    # Binding write only on the successful (second) persist.
    binding_repo.update_status.assert_called_once_with(binding_id=77, status="ACTIVE")
    assert baas.get_publish_progress.call_count == 2


@pytest.mark.unit
def test_persist_always_failing_gives_up_without_raising() -> None:
    svc, baas, bot_repo, binding_repo, _clock, scheduler = _make(
        poll_interval_s=10.0, timeout_s=15.0
    )
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}
    bot_repo.update_by_owner.side_effect = RuntimeError("db permanently down")

    svc.start(publish_id=9, bot_id="b1", owner_id="u1", binding_id=77)
    scheduler.drive()

    assert bot_repo.update_by_owner.call_count == 3  # t=0,10 reschedule; t=20 gives up
    binding_repo.update_status.assert_not_called()


# ── the real _DelayedScheduler (tiny delays, no fakes) ────────────────────────


@pytest.mark.unit
def test_delayed_scheduler_runs_task_on_daemon_thread() -> None:
    from agentclaw.community.core.bot_management.services.teclaw_status_reconciler import (
        _DelayedScheduler,
    )

    sched = _DelayedScheduler()
    done = threading.Event()
    seen = {}

    def _task() -> None:
        seen["daemon"] = threading.current_thread().daemon
        done.set()

    sched.schedule(_task, 0.0)

    assert done.wait(timeout=2.0)
    assert seen["daemon"] is True


@pytest.mark.unit
def test_delayed_scheduler_supports_self_rescheduling() -> None:
    # A task that reschedules itself runs repeatedly on the single shared thread
    # — the wait between runs is held in the heap, never by a parked worker.
    from agentclaw.community.core.bot_management.services.teclaw_status_reconciler import (
        _DelayedScheduler,
    )

    sched = _DelayedScheduler()
    done = threading.Event()
    runs = {"n": 0}

    def _task() -> None:
        runs["n"] += 1
        if runs["n"] < 3:
            sched.schedule(_task, 0.001)
        else:
            done.set()

    sched.schedule(_task, 0.0)

    assert done.wait(timeout=2.0)
    assert runs["n"] == 3


@pytest.mark.unit
def test_delayed_scheduler_survives_a_raising_task() -> None:
    # One bad task must not wedge the loop or block later tasks.
    from agentclaw.community.core.bot_management.services.teclaw_status_reconciler import (
        _DelayedScheduler,
    )

    sched = _DelayedScheduler()
    done = threading.Event()

    sched.schedule(lambda: (_ for _ in ()).throw(RuntimeError("boom")), 0.0)
    sched.schedule(done.set, 0.0)

    assert done.wait(timeout=2.0)
