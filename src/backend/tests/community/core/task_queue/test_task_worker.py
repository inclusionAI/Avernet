"""Integration tests for TaskWorker against real in-memory SQLite + registry.

Drives the worker via its ``run_once()`` seam (no sleeping in the loop).
Timing is DB-owned, so deadline behavior is exercised via ``deadline_seconds``
and backoff config rather than injected clocks.
"""
import asyncio
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.bot_management.services.teclaw_publish_task_handler import (
    TECLAW_CREATE_PUBLISH_POLL_TASK,
    TeclawPublishTaskHandler,
    build_teclaw_publish_poll_payload,
)
from agentclaw.community.core.task_queue.examples import (
    NoopTaskHandler,
    PollUntilTerminalExampleHandler,
)
from agentclaw.community.core.task_queue.repository.models import TaskQueueModel  # noqa: F401
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.core.task_queue.services.wakeup import WorkerWakeup
from agentclaw.community.core.task_queue.services.worker import TaskWorker
from agentclaw.community.core.task_queue.types import DEFAULT_APP, Complete, TaskStatus
from agentclaw.community.di.config import TaskQueueConfig, TaskQueueWorkerConfig
from agentclaw.community.core.repository.implementations.platform.task_queue import TaskQueueRepository

pytestmark = pytest.mark.integration

ENV = "dev"
APP = DEFAULT_APP


class InMemorySqliteDB:
    def __init__(self, engine):
        self._session_factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


class _World:
    def __init__(self, config: TaskQueueWorkerConfig):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        from agentclaw.community.core.base import Base

        Base.metadata.create_all(engine)
        self.repo = TaskQueueRepository(InMemorySqliteDB(engine))
        self.registry = HandlerRegistry()
        self.config = config
        # The owning app, shared by both sides exactly as DI shares it: the
        # enqueue path stamps it and the worker claims with it, so a mismatch
        # here would show up as a worker that claims nothing.
        self.queue_config = TaskQueueConfig(app=APP)
        # One latch, shared by the enqueue path and the worker — same wiring
        # the DI module provides in production.
        self.wakeup = WorkerWakeup()
        self.service = TaskQueueService(
            self.repo, self.registry, self.wakeup, self.queue_config
        )
        self.worker = TaskWorker(
            self.repo, self.registry, config, self.wakeup, self.queue_config
        )

    def enqueue(
        self,
        task_type,
        payload=None,
        *,
        deadline_seconds=3600,
        delay_seconds=0,
        idempotency_key=None,
    ):
        """Just the ``TaskRecord``. These tests predate enqueue idempotency and
        pass no key, so they double as a regression guard that un-keyed enqueue
        behaves exactly as it always has."""
        return self.service.enqueue(
            task_type,
            payload if payload is not None else {},
            deadline_seconds,
            delay_seconds=delay_seconds,
            idempotency_key=idempotency_key,
        ).record

    def status_of(self, task_id):
        return self.repo.get_by_id(task_id).status


def _world(**overrides) -> _World:
    cfg = TaskQueueWorkerConfig(
        enabled=overrides.pop("enabled", True),
        batch_size=overrides.pop("batch_size", 10),
        lease_seconds=overrides.pop("lease_seconds", 60),
        max_concurrency=overrides.pop("max_concurrency", 10),
        retry_backoff_min_seconds=overrides.pop("retry_backoff_min_seconds", 1.0),
        retry_backoff_max_seconds=overrides.pop("retry_backoff_max_seconds", 60.0),
        **overrides,
    )
    return _World(cfg)


# ── happy path ──────────────────────────────────────────────────────────────

def test_noop_handler_completes_in_one_tick():
    w = _world()
    w.registry.register(NoopTaskHandler())
    rec = w.enqueue("noop")
    claimed = asyncio.run(w.worker.run_once())
    assert claimed == 1
    assert w.status_of(rec.id) == TaskStatus.SUCCEEDED


def test_missing_handler_marks_failed():
    w = _world()
    rec = w.enqueue("no_such_type")
    asyncio.run(w.worker.run_once())
    stored = w.repo.get_by_id(rec.id)
    assert stored.status == TaskStatus.FAILED
    assert "no handler registered" in stored.last_error


def test_worker_leaves_another_apps_task_alone():
    """End to end over the seam that matters once the table is shared: a second
    backend's row is enqueued with its own app, and this worker must not touch
    it — the two tests above show what would otherwise happen to it, since its
    ``task_type`` is not in this process's registry and the worker would fail it
    terminally."""
    w = _world()
    w.registry.register(NoopTaskHandler())
    theirs = w.repo.enqueue(
        task_type="noop",
        payload={},
        delay_seconds=0,
        deadline_seconds=3600,
        env=ENV,
        app="teclaw",
        idempotency_key=None,
    ).record

    assert asyncio.run(w.worker.run_once()) == 0
    assert w.status_of(theirs.id) == TaskStatus.PENDING


# ── poll-until-terminal (the motivating shape) ──────────────────────────────

def test_poll_until_terminal_reschedules_then_completes():
    w = _world()
    calls = {"n": 0}

    def status_fn(payload):
        calls["n"] += 1
        return "SUCCESS" if calls["n"] >= 3 else "RUNNING"

    w.registry.register(
        PollUntilTerminalExampleHandler(
            status_fn,
            success_states={"SUCCESS"},
            failure_states={"FAILED"},
            task_type="poll",
            delay_seconds=0.0,  # immediately re-due so run_once can drive it
        )
    )
    rec = w.enqueue("poll", {"publish_id": 1})

    async def drive():
        await w.worker.run_once()  # tick 1: not terminal → back to PENDING
        assert w.status_of(rec.id) == TaskStatus.PENDING
        for _ in range(5):
            if w.status_of(rec.id) == TaskStatus.SUCCEEDED:
                break
            await w.worker.run_once()

    asyncio.run(drive())
    assert w.status_of(rec.id) == TaskStatus.SUCCEEDED
    assert calls["n"] == 3


def test_poll_until_terminal_fails_on_failure_state():
    w = _world()
    w.registry.register(
        PollUntilTerminalExampleHandler(
            lambda payload: "FAILED",
            success_states={"SUCCESS"},
            failure_states={"FAILED"},
            task_type="poll",
        )
    )
    rec = w.enqueue("poll")
    asyncio.run(w.worker.run_once())
    stored = w.repo.get_by_id(rec.id)
    assert stored.status == TaskStatus.FAILED
    assert "FAILED" in stored.last_error


# ── retry / deadline ────────────────────────────────────────────────────────

class _BoomHandler:
    task_type = "boom"

    def handle(self, payload):
        raise RuntimeError("kaboom")


def test_raising_handler_retries_with_backoff_within_deadline():
    w = _world(retry_backoff_min_seconds=1.0, lease_seconds=60)
    w.registry.register(_BoomHandler())
    rec = w.enqueue("boom", deadline_seconds=3600)
    asyncio.run(w.worker.run_once())
    stored = w.repo.get_by_id(rec.id)
    # Retried (back to PENDING) with the error recorded.
    assert stored.status == TaskStatus.PENDING
    assert "kaboom" in stored.last_error


def test_raising_handler_times_out_when_backoff_exceeds_deadline():
    # Deadline 1s but backoff floor 100s → the retry would land past the
    # deadline, so the repository marks it TIMED_OUT instead of rescheduling.
    w = _world(retry_backoff_min_seconds=100.0)
    w.registry.register(_BoomHandler())
    rec = w.enqueue("boom", deadline_seconds=1)
    asyncio.run(w.worker.run_once())
    assert w.status_of(rec.id) == TaskStatus.TIMED_OUT


def test_past_deadline_task_times_out_at_claim_without_running():
    w = _world()
    ran = {"called": False}

    class _Tracker:
        task_type = "track"

        def handle(self, payload):
            ran["called"] = True
            return Complete()

    w.registry.register(_Tracker())
    rec = w.enqueue("track", deadline_seconds=0)  # deadline == now()
    claimed = asyncio.run(w.worker.run_once())
    assert claimed == 0  # claim timed it out, never handed it over
    assert w.status_of(rec.id) == TaskStatus.TIMED_OUT
    assert ran["called"] is False


# ── greedy re-poll signal ───────────────────────────────────────────────────

def test_full_batch_returns_batch_size_so_loop_repolls():
    w = _world(batch_size=3)
    w.registry.register(NoopTaskHandler())
    for _ in range(7):
        w.enqueue("noop")

    async def drive():
        return [await w.worker.run_once() for _ in range(3)]

    counts = asyncio.run(drive())
    assert counts == [3, 3, 1]
    assert len(w.repo.list_by_status(status=TaskStatus.SUCCEEDED, env=ENV, app=APP)) == 7


def test_disabled_worker_startup_is_noop():
    w = _world(enabled=False)
    w.registry.register(NoopTaskHandler())
    w.enqueue("noop")

    async def boot():
        await w.worker.startup()
        await w.worker.shutdown()

    asyncio.run(boot())
    assert len(w.repo.list_by_status(status=TaskStatus.SUCCEEDED, env=ENV, app=APP)) == 0


# ── lease-renewal heartbeat ─────────────────────────────────────────────────

def test_heartbeat_renews_lease_during_long_handler(monkeypatch):
    """A handler that runs longer than the lease keeps its claim (renewed by the
    heartbeat) and its outcome write still succeeds."""
    w = _world(lease_seconds=1)

    calls = []
    orig_renew = w.repo.renew_lease

    def _spy(**kwargs):
        calls.append(kwargs)
        return orig_renew(**kwargs)

    monkeypatch.setattr(w.repo, "renew_lease", _spy)

    class _SlowHandler:
        @property
        def task_type(self):
            return "slow"

        def handle(self, payload):
            time.sleep(2.5)  # > lease_seconds=1 → heartbeat must renew
            return Complete()

    w.registry.register(_SlowHandler())
    rec = w.enqueue("slow")

    asyncio.run(w.worker.run_once())

    # Heartbeat fired at least once during the 2.5s run, and because the lease
    # stayed with this worker the terminal Complete write succeeded.
    assert len(calls) >= 1
    assert w.status_of(rec.id) == TaskStatus.SUCCEEDED

def test_teclaw_publish_task_is_reclaimed_after_worker_restart():
    w = _world(lease_seconds=0)
    baas = MagicMock()
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = SimpleNamespace(
        id=77,
        status="PENDING",
        device_provider="teclaw",
        device_props={"publish_id": 9},
        device_id="BOT-x",
        entity_id="staff-1",
    )
    w.registry.register(
        TeclawPublishTaskHandler(
            baas_service=baas,
            device_binding_repo=binding_repo,
            passport_plugin=MagicMock(),
            credentials_admins_writer=MagicMock(),
        )
    )
    record = w.enqueue(
        TECLAW_CREATE_PUBLISH_POLL_TASK,
        build_teclaw_publish_poll_payload(
            binding_id=77,
            bot_id="b1",
            owner_id="u1",
            publish_id=9,
            started_at_epoch_s=time.time(),
        ),
    )
    abandoned = w.repo.claim_batch(
        worker_id="dead-worker",
        env=ENV,
        app=APP,
        limit=1,
        lease_seconds=0,
    )
    assert [task.id for task in abandoned] == [record.id]

    restarted_worker = TaskWorker(
        w.repo, w.registry, w.config, w.wakeup, w.queue_config
    )
    asyncio.run(restarted_worker.run_once())

    assert w.status_of(record.id) == TaskStatus.SUCCEEDED
    binding_repo.transition_teclaw_publish_terminal.assert_called_once_with(
        binding_id=77,
        bot_id="b1",
        owner_id="u1",
        publish_id=9,
        status="ACTIVE",
    )


# ── opt-in immediate execution ──────────────────────────────────────────────
# A task type may ask for its enqueues to wake the worker at once instead of
# waiting out the idle poll. Off by default, so every pre-existing type keeps
# the timing it already had — that default is what most of these tests pin down.


class _RecordingWakeup:
    """Stands in for ``WorkerWakeup`` to count signals without a live loop."""

    def __init__(self):
        self.notifies = 0

    def notify(self):
        self.notifies += 1


def _service_with(world, wakeup):
    """A service over ``world``'s repo/registry but a countable latch."""
    return TaskQueueService(world.repo, world.registry, wakeup, world.queue_config)


def test_registry_defaults_to_not_waking_on_enqueue():
    """The default is opt-out: registering a handler the way every existing
    adopter does must leave its task type on the ordinary poll cadence."""
    registry = HandlerRegistry()
    registry.register(NoopTaskHandler("legacy"))
    assert registry.wakes_on_enqueue("legacy") is False


def test_registry_records_an_explicit_opt_in():
    registry = HandlerRegistry()
    registry.register(NoopTaskHandler("urgent"), wake_on_enqueue=True)
    assert registry.wakes_on_enqueue("urgent") is True


def test_registry_reports_no_wake_for_an_unregistered_type():
    """Only a process that can actually run the type benefits from waking, so
    an unknown type is a plain False rather than an error."""
    assert HandlerRegistry().wakes_on_enqueue("never-registered") is False


def test_opted_in_type_signals_the_worker_on_enqueue():
    w = _world()
    w.registry.register(NoopTaskHandler("urgent"), wake_on_enqueue=True)
    wakeup = _RecordingWakeup()
    _service_with(w, wakeup).enqueue("urgent", {}, 3600)
    assert wakeup.notifies == 1


def test_type_that_did_not_opt_in_is_left_on_the_poll_cadence():
    """The core guarantee for existing task types: nothing about their timing
    changes."""
    w = _world()
    w.registry.register(NoopTaskHandler("legacy"))
    wakeup = _RecordingWakeup()
    _service_with(w, wakeup).enqueue("legacy", {}, 3600)
    assert wakeup.notifies == 0


def test_delayed_enqueue_does_not_signal_even_when_opted_in():
    """A delayed task has ``run_at > now()``, so it fails the claim's
    eligibility predicate — waking would burn a poll and change nothing."""
    w = _world()
    w.registry.register(NoopTaskHandler("urgent"), wake_on_enqueue=True)
    wakeup = _RecordingWakeup()
    _service_with(w, wakeup).enqueue("urgent", {}, 3600, delay_seconds=30)
    assert wakeup.notifies == 0


def test_keyed_enqueue_that_joined_a_live_task_does_not_signal():
    """``created=False`` means no work was added — the holder is already
    pending or running, so there is nothing new for a wake to pick up."""
    w = _world()
    w.registry.register(NoopTaskHandler("urgent"), wake_on_enqueue=True)
    wakeup = _RecordingWakeup()
    service = _service_with(w, wakeup)

    first = service.enqueue("urgent", {}, 3600, idempotency_key="k1")
    second = service.enqueue("urgent", {}, 3600, idempotency_key="k1")

    assert first.created is True
    assert second.created is False
    assert second.record.id == first.record.id
    assert wakeup.notifies == 1  # the create signalled; the join did not


def test_signalling_never_breaks_the_enqueue_contract():
    """A latency optimisation must not change what enqueue returns or persists."""
    w = _world()
    w.registry.register(NoopTaskHandler("urgent"), wake_on_enqueue=True)
    record = _service_with(w, _RecordingWakeup()).enqueue(
        "urgent", {"a": 1}, 3600
    ).record
    assert record.task_type == "urgent"
    assert record.payload == {"a": 1}
    assert w.status_of(record.id) == TaskStatus.PENDING


def test_enqueue_survives_a_wakeup_that_has_no_loop_bound():
    """End-to-end with the real latch and no running worker: the notify is a
    no-op and the task is still enqueued for the next poll to claim."""
    w = _world()
    w.registry.register(NoopTaskHandler("urgent"), wake_on_enqueue=True)
    record = w.enqueue("urgent")  # w.wakeup is real and unbound
    assert w.status_of(record.id) == TaskStatus.PENDING
    asyncio.run(w.worker.run_once())
    assert w.status_of(record.id) == TaskStatus.SUCCEEDED


def test_idle_worker_loop_wakes_on_an_opted_in_enqueue(monkeypatch):
    """The whole feature, end to end through the real loop: with a poll interval
    far longer than the test could tolerate, an enqueue still gets claimed
    promptly because it cuts the idle wait short."""
    w = _world(poll_interval_seconds=30.0, poll_jitter_seconds=0.0)

    async def drive():
        idle_wait_entered = asyncio.Event()
        task_claimed = asyncio.Event()
        loop = asyncio.get_running_loop()
        original_wait = w.wakeup.wait

        async def wait(timeout):
            idle_wait_entered.set()
            return await original_wait(timeout)

        class SignallingHandler:
            task_type = "urgent"

            def handle(self, payload):
                loop.call_soon_threadsafe(task_claimed.set)
                return Complete()

        monkeypatch.setattr(w.wakeup, "wait", wait)
        w.registry.register(SignallingHandler(), wake_on_enqueue=True)
        await w.worker.startup()
        try:
            # Wait until the initial empty tick is interruptibly idle rather
            # than racing it with a fixed sleep.
            await asyncio.wait_for(idle_wait_entered.wait(), timeout=1.0)
            # The cross-thread notify behavior is covered in
            # test_worker_wakeup.py. Keeping this SQLite-backed integration
            # test on one thread avoids concurrent use of its StaticPool
            # connection while still exercising the real worker loop.
            w.enqueue("urgent")
            started = time.monotonic()
            await asyncio.wait_for(task_claimed.wait(), timeout=1.0)
            return time.monotonic() - started
        finally:
            await w.worker.shutdown()

    elapsed = asyncio.run(drive())
    assert elapsed < 1.0
