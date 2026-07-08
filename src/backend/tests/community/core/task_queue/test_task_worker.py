"""Integration tests for TaskWorker against real in-memory SQLite + registry.

Drives the worker via its ``run_once()`` seam (no sleeping in the loop).
Timing is DB-owned, so deadline behavior is exercised via ``deadline_seconds``
and backoff config rather than injected clocks.
"""
import asyncio
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.task_queue.examples import (
    NoopTaskHandler,
    PollUntilTerminalExampleHandler,
)
from agentclaw.community.core.task_queue.repository.models import TaskQueueModel  # noqa: F401
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.core.task_queue.services.worker import TaskWorker
from agentclaw.community.core.task_queue.types import Complete, TaskStatus
from agentclaw.community.di.config import TaskQueueWorkerConfig
from agentclaw.community.plugins.task_queue_repository import TaskQueueRepository

pytestmark = pytest.mark.integration

ENV = "dev"


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
        self.service = TaskQueueService(self.repo)
        self.worker = TaskWorker(self.repo, self.registry, config)

    def enqueue(self, task_type, payload=None, *, deadline_seconds=3600, delay_seconds=0):
        return self.service.enqueue(
            task_type,
            payload if payload is not None else {},
            deadline_seconds,
            delay_seconds=delay_seconds,
        )

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
    assert len(w.repo.list_by_status(status=TaskStatus.SUCCEEDED, env=ENV)) == 7


def test_disabled_worker_startup_is_noop():
    w = _world(enabled=False)
    w.registry.register(NoopTaskHandler())
    w.enqueue("noop")

    async def boot():
        await w.worker.startup()
        await w.worker.shutdown()

    asyncio.run(boot())
    assert len(w.repo.list_by_status(status=TaskStatus.SUCCEEDED, env=ENV)) == 0
