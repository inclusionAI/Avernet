"""The trace of the enqueuing request, carried across the queue.

A task is submitted by one request and run later by another process, so the two
halves land in the logs under different traces — or under none at all — unless
the trace is persisted with the row and re-established when the worker picks it
up. These tests pin that round trip end to end: a real tracer on both sides, a
real SQLite row in between.

``CommunityTracer`` rather than a hand-rolled double on purpose. It is a real
``TracerPlugin`` with a real ``ContextVar``, so the tests exercise the same
export → persist → restore path prod uses; only the id minting differs. The
corp impl's carrier holds SofaTracer's propagation headers instead, which is
exactly why nothing here reads a carrier's contents.
"""
import asyncio
import threading
from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.repository.implementations.platform.task_queue import (
    TaskQueueRepository,
)
from agentclaw.community.core.task_queue.repository.models import TaskQueueModel
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.task_queue.services.wakeup import WorkerWakeup
from agentclaw.community.core.task_queue.services.worker import TaskWorker
from agentclaw.community.core.task_queue.types import (
    MAX_TRACE_ID_LEN,
    Complete,
    DEFAULT_APP,
    TaskStatus,
)
from agentclaw.community.di.config import TaskQueueConfig, TaskQueueWorkerConfig
from agentclaw.community.plugins.community.tracer import CommunityTracer
from agentclaw.community.plugins.local.tracer import NoopTracer

pytestmark = pytest.mark.integration

ENV = "dev"
APP = DEFAULT_APP


class _SqliteDB:
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


class _RecordingHandler:
    """Records the trace id visible to the handler body, then completes.

    Reading through the tracer rather than off the payload is the point: it is
    the *ambient* trace the handler's own logging would pick up, which is the
    thing the feature exists to restore.
    """

    def __init__(self, tracer, task_type="traced", before=None):
        self.task_type = task_type
        self._tracer = tracer
        self._before = before
        self.seen = []

    def handle(self, payload):
        if self._before is not None:
            self._before()
        self.seen.append(self._tracer.current_trace_id())
        return Complete()


class _World:
    def __init__(self, tracer, max_concurrency=4):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        from agentclaw.community.core.base import Base

        Base.metadata.create_all(engine)
        self.db = _SqliteDB(engine)
        self.repo = TaskQueueRepository(self.db)
        self.registry = HandlerRegistry()
        self.wakeup = WorkerWakeup()
        self.queue_config = TaskQueueConfig(app=APP)
        self.tracer = tracer
        self.service = TaskQueueService(
            self.repo, self.registry, self.wakeup, self.queue_config, tracer
        )
        self.worker = TaskWorker(
            self.repo,
            self.registry,
            TaskQueueWorkerConfig(
                enabled=True, batch_size=10, max_concurrency=max_concurrency
            ),
            self.wakeup,
            self.queue_config,
            tracer,
        )

    def enqueue(self, task_type="traced", **kwargs):
        return self.service.enqueue(task_type, {}, 3600, **kwargs).record

    def row(self, task_id):
        with self.db.orm_session() as db:
            row = db.query(TaskQueueModel).filter_by(id=task_id).one()
            return row.trace_id, row.trace_carrier

    def run(self):
        return asyncio.run(self.worker.run_once())


@pytest.fixture
def env(monkeypatch):
    for module in (
        "agentclaw.community.core.task_queue.services.task_queue_service",
        "agentclaw.community.core.task_queue.services.worker",
    ):
        monkeypatch.setattr(f"{module}.get_current_env", lambda: ENV)


@pytest.fixture
def world(env):
    return _World(CommunityTracer())


# ── the round trip ──────────────────────────────────────────────────────────


def test_enqueue_stores_the_trace_of_the_calling_request(world):
    """Captured at the service, which every adopter goes through — so no call
    site had to be touched to gain this."""
    with world.tracer.trace_scope({"trace_id": "trace-abc"}):
        record = world.enqueue()

    trace_id, carrier = world.row(record.id)
    assert trace_id == "trace-abc"
    # The carrier is the tracer's business, not the queue's; all this layer
    # promises is that what was exported comes back unchanged.
    assert carrier is not None
    assert record.trace_id == "trace-abc"


def test_handler_runs_under_the_enqueuing_requests_trace(world):
    """The whole point: work submitted by a request logs under that request's
    trace when it runs, minutes later and in another process."""
    handler = _RecordingHandler(world.tracer)
    world.registry.register(handler)

    with world.tracer.trace_scope({"trace_id": "trace-xyz"}):
        record = world.enqueue()

    # Nothing is active out here — exactly the worker's situation.
    assert world.tracer.current_trace_id() is None
    assert world.run() == 1

    assert handler.seen == ["trace-xyz"]
    assert world.repo.get_by_id(record.id).status == TaskStatus.SUCCEEDED
    # ...and the restore did not outlive the task it was restored for.
    assert world.tracer.current_trace_id() is None


def test_concurrent_tasks_each_keep_their_own_trace(world):
    """Handlers run concurrently under one worker, so a restored trace has to be
    private to its task. Both handlers are held inside their scope at the same
    time, which is precisely when a shared binding would show."""
    barrier = threading.Barrier(2, timeout=10)
    handler = _RecordingHandler(world.tracer, before=barrier.wait)
    world.registry.register(handler)

    for trace in ("trace-1", "trace-2"):
        with world.tracer.trace_scope({"trace_id": trace}):
            world.enqueue()

    assert world.run() == 2
    assert sorted(handler.seen) == ["trace-1", "trace-2"]


# ── absence is normal, not a defect ─────────────────────────────────────────


def test_enqueue_outside_a_request_stores_no_trace(world):
    """Background work — a handler enqueuing a follow-up, a boot-time enqueue —
    has no trace to carry. The columns stay NULL and the task runs regardless."""
    handler = _RecordingHandler(world.tracer)
    world.registry.register(handler)

    record = world.enqueue()
    assert world.row(record.id) == (None, None)

    assert world.run() == 1
    assert handler.seen == [None]
    assert world.repo.get_by_id(record.id).status == TaskStatus.SUCCEEDED


def test_a_tracer_that_traces_nothing_changes_nothing(env):
    """The local/test binding. Enqueue and execution behave exactly as they did
    before trace carriage existed."""
    world = _World(NoopTracer())
    handler = _RecordingHandler(world.tracer)
    world.registry.register(handler)

    record = world.enqueue()
    assert world.row(record.id) == (None, None)
    assert world.run() == 1
    assert world.repo.get_by_id(record.id).status == TaskStatus.SUCCEEDED


# ── correlation must never cost the caller its work ─────────────────────────


class _CompletingHandler:
    """Completes, and touches no tracer — for the tests whose tracer is broken."""

    task_type = "traced"

    def __init__(self):
        self.runs = 0

    def handle(self, payload):
        self.runs += 1
        return Complete()


class _BrokenExportTracer(NoopTracer):
    """Raises on the enqueue side, where the protocol says it must not."""

    def current_trace_id(self):
        raise RuntimeError("tracer is having a bad day")

    def export_trace_carrier(self):
        raise RuntimeError("tracer is having a bad day")


class _BrokenScopeTracer(NoopTracer):
    """Raises on the execution side, entering the restored scope."""

    def export_trace_carrier(self):
        return {"trace_id": "trace-abc"}

    def trace_scope(self, carrier):
        raise RuntimeError("tracer is having a bad day")


def test_a_broken_tracer_does_not_fail_the_enqueue(env):
    """A diagnostic that can lose a caller's work is worse than no diagnostic.
    The task is enqueued and runs; only the correlation is missing."""
    world = _World(_BrokenExportTracer())
    handler = _CompletingHandler()
    world.registry.register(handler)

    record = world.enqueue()
    assert world.row(record.id) == (None, None)
    assert world.run() == 1
    assert handler.runs == 1
    assert world.repo.get_by_id(record.id).status == TaskStatus.SUCCEEDED


def test_a_tracer_that_cannot_restore_does_not_stall_the_batch(env):
    """The failure mode this guard exists for. A raise from ``trace_scope``
    would otherwise escape ``_run_guarded``, abort the ``gather`` running the
    batch, and leave *every* task in it RUNNING until its lease expired — with
    the next poll repeating the whole thing. Both tasks here run and complete."""
    world = _World(_BrokenScopeTracer())
    handler = _CompletingHandler()
    world.registry.register(handler)

    first = world.enqueue()
    second = world.enqueue()

    assert world.run() == 2
    assert handler.runs == 2
    for record in (first, second):
        assert world.repo.get_by_id(record.id).status == TaskStatus.SUCCEEDED


def test_an_overlong_trace_id_is_truncated_rather_than_rejected(env):
    """Unlike an idempotency key, which raises: a truncated key silently merges
    two dedup scopes and can drop work, while a truncated trace id is only a
    less useful log line. The engines disagree about overflow, so the bound is
    applied in Python where the behaviour is the same everywhere."""

    class _LongIdTracer(CommunityTracer):
        def current_trace_id(self):
            return "t" * (MAX_TRACE_ID_LEN + 50)

    world = _World(_LongIdTracer())
    with world.tracer.trace_scope({"trace_id": "ignored"}):
        record = world.enqueue()

    trace_id, _ = world.row(record.id)
    assert trace_id == "t" * MAX_TRACE_ID_LEN


def test_an_unserializable_carrier_does_not_fail_the_enqueue(env):
    """The write-side mirror of the unreadable-carrier case below.

    A carrier can be well-formed enough to export and still not survive
    ``json.dumps`` — a datetime, an SDK object, a cycle. That serialization
    happens inside ``orm_session()``, so an unguarded raise would roll the insert
    back and propagate out of ``TaskQueueService.enqueue``, costing the caller the
    work it asked for rather than just its correlation. The id still lands; only
    the carrier is dropped.
    """

    class _UnserializableCarrierTracer(CommunityTracer):
        def export_trace_carrier(self):
            return {"trace_id": "trace-abc", "exported_at": datetime.now()}

    world = _World(_UnserializableCarrierTracer())
    handler = _CompletingHandler()
    world.registry.register(handler)

    with world.tracer.trace_scope({"trace_id": "trace-abc"}):
        record = world.enqueue()

    trace_id, carrier = world.row(record.id)
    assert trace_id == "trace-abc"
    assert carrier is None

    assert world.run() == 1
    assert handler.runs == 1
    assert world.repo.get_by_id(record.id).status == TaskStatus.SUCCEEDED


def test_an_unreadable_carrier_does_not_strand_the_task(world):
    """``claim_batch`` projects a whole batch, so a row that raised while being
    read would take its batch down — and keep doing so on every poll. A carrier
    that cannot be parsed degrades to no trace; the task still runs."""
    handler = _RecordingHandler(world.tracer)
    world.registry.register(handler)
    record = world.enqueue()

    with world.db.orm_session() as db:
        db.query(TaskQueueModel).filter_by(id=record.id).update(
            {"trace_carrier": "}{ not json"}
        )

    assert world.run() == 1
    assert handler.seen == [None]
    assert world.repo.get_by_id(record.id).status == TaskStatus.SUCCEEDED


# ── interaction with enqueue idempotency ────────────────────────────────────


def test_a_keyed_duplicate_keeps_the_trace_that_created_the_task(world):
    """A duplicate inserts no row, so it cannot re-stamp one. The work runs once,
    under the trace that caused it to exist; the joining request is followable
    through the join log line, which carries both ids."""
    with world.tracer.trace_scope({"trace_id": "trace-first"}):
        first = world.enqueue(idempotency_key="k1")

    with world.tracer.trace_scope({"trace_id": "trace-second"}):
        joined = world.service.enqueue("traced", {}, 3600, idempotency_key="k1")

    assert joined.created is False
    assert joined.record.id == first.id
    assert world.row(first.id)[0] == "trace-first"
