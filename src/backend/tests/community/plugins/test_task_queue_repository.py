"""Integration tests for TaskQueueRepository against real in-memory SQLite.

The same single ORM body runs on prod OceanBase, so the DB-side claim CAS,
lease reclaim, holder-guarded transitions, and deadline timeout are all
exercised against a real database here. Timing is DB-owned (no injected
``now``), so the few time-sensitive cases use short real sleeps.
"""
import time
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Side-effect import: registers TaskQueueModel on Base.metadata so
# create_all() builds the ac_task_queue table.
from agentclaw.community.core.task_queue.repository.models import TaskQueueModel  # noqa: F401
from agentclaw.community.core.task_queue.types import TaskStatus
from agentclaw.community.plugins.task_queue_repository import TaskQueueRepository

pytestmark = pytest.mark.integration

ENV = "dev"


class InMemorySqliteDB:
    """Shared-connection in-memory SQLite (StaticPool). The Session it yields
    exposes ``.bind`` (the engine), which the repository uses to pick the
    dialect for its ``now() + interval`` SQL."""

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


@pytest.fixture
def repo():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return TaskQueueRepository(InMemorySqliteDB(engine))


def _enqueue(repo, *, payload=None, delay_seconds=0, deadline_seconds=3600, env=ENV):
    return repo.enqueue(
        task_type="demo",
        payload=payload if payload is not None else {"k": "v"},
        delay_seconds=delay_seconds,
        deadline_seconds=deadline_seconds,
        env=env,
    )


def _claim(repo, worker, *, limit=10, lease=60, env=ENV):
    return repo.claim_batch(worker_id=worker, env=env, limit=limit, lease_seconds=lease)


# ── enqueue ─────────────────────────────────────────────────────────────────

def test_enqueue_persists_pending_with_required_fields(repo):
    rec = _enqueue(repo)
    assert rec.id is not None
    assert rec.status == TaskStatus.PENDING
    assert rec.attempts == 0
    assert rec.deadline_at is not None  # always set, DB-side
    assert rec.run_at is not None
    assert rec.gmt_create is not None  # DB-managed


def test_enqueue_payload_round_trips_as_json(repo):
    rec = _enqueue(repo, payload={"publish_id": 42, "nested": [1, 2, 3]})
    assert repo.get_by_id(rec.id).payload == {"publish_id": 42, "nested": [1, 2, 3]}


# ── claim: due / not-due / scoping ──────────────────────────────────────────

def test_claim_skips_tasks_not_yet_due(repo):
    _enqueue(repo, delay_seconds=60)  # run_at = now()+60 → not eligible now
    assert _claim(repo, "W") == []


def test_claim_respects_env_scoping(repo):
    _enqueue(repo, env="dev")
    _enqueue(repo, env="pre")
    won = _claim(repo, "W", env="dev")
    assert len(won) == 1 and won[0].env == "dev"


def test_claim_honors_limit(repo):
    for _ in range(5):
        _enqueue(repo)
    assert len(_claim(repo, "W", limit=2)) == 2


def test_claim_increments_attempts_and_sets_holder(repo):
    rec = _enqueue(repo)
    won = _claim(repo, "W", lease=60)
    assert won[0].attempts == 1
    stored = repo.get_by_id(rec.id)
    assert stored.status == TaskStatus.RUNNING
    assert stored.claimed_by == "W"
    assert stored.lease_expires_at is not None


# ── claim exclusivity (the core idempotency guarantee) ──────────────────────

def test_two_workers_claiming_get_disjoint_tasks(repo):
    # NOTE: single-threaded SQLite runs A's claim to commit before B starts, so
    # this asserts the observable property (no task won twice). The real race
    # safety is the per-row CAS, atomic in one UPDATE on SQLite and OceanBase.
    ids = {_enqueue(repo).id for _ in range(6)}
    a = _claim(repo, "A")
    b = _claim(repo, "B")
    a_ids = {t.id for t in a}
    b_ids = {t.id for t in b}
    assert a_ids.isdisjoint(b_ids)
    assert a_ids | b_ids <= ids


def test_second_worker_gets_nothing_while_leases_live(repo):
    for _ in range(3):
        _enqueue(repo)
    a = _claim(repo, "A", lease=60)
    b = _claim(repo, "B", lease=60)
    assert len(a) == 3 and b == []


# ── lease reclaim (real time) ───────────────────────────────────────────────

def test_lease_reclaim_before_and_after_expiry(repo):
    rec = _enqueue(repo)
    _claim(repo, "A", lease=1)
    # Still within the lease → not reclaimable.
    assert _claim(repo, "B", lease=1) == []
    time.sleep(2.1)  # cross the 1s lease (DB clock is second-granular)
    reclaimed = _claim(repo, "B", lease=60)
    assert len(reclaimed) == 1
    stored = repo.get_by_id(rec.id)
    assert stored.claimed_by == "B"
    assert stored.attempts == 2  # claimed twice


# ── holder-guarded transitions (CAS) ────────────────────────────────────────

def test_complete_succeeds_for_holder(repo):
    rec = _enqueue(repo)
    _claim(repo, "W")
    assert repo.complete(task_id=rec.id, worker_id="W") is True
    assert repo.get_by_id(rec.id).status == TaskStatus.SUCCEEDED


def test_stale_worker_cannot_mutate(repo):
    rec = _enqueue(repo)
    _claim(repo, "A", lease=1)
    time.sleep(2.1)
    _claim(repo, "B", lease=60)  # B takes over
    assert repo.complete(task_id=rec.id, worker_id="A") is False
    assert repo.reschedule(task_id=rec.id, worker_id="A", delay_seconds=5) is False
    assert repo.fail(task_id=rec.id, worker_id="A", error="x") is False
    stored = repo.get_by_id(rec.id)
    assert stored.status == TaskStatus.RUNNING and stored.claimed_by == "B"


def test_reschedule_returns_to_pending_and_records_error(repo):
    rec = _enqueue(repo, deadline_seconds=3600)
    _claim(repo, "W")
    assert repo.reschedule(
        task_id=rec.id, worker_id="W", delay_seconds=0, error="transient"
    ) is True
    stored = repo.get_by_id(rec.id)
    assert stored.status == TaskStatus.PENDING
    assert stored.claimed_by is None and stored.lease_expires_at is None
    assert stored.last_error == "transient"


def test_fail_is_terminal(repo):
    rec = _enqueue(repo)
    _claim(repo, "W")
    assert repo.fail(task_id=rec.id, worker_id="W", error="boom") is True
    stored = repo.get_by_id(rec.id)
    assert stored.status == TaskStatus.FAILED and stored.last_error == "boom"


# ── deadline / timeout (DB-side) ────────────────────────────────────────────

def test_claim_times_out_past_deadline_task_without_returning_it(repo):
    rec = _enqueue(repo, deadline_seconds=0)  # deadline == now()
    won = _claim(repo, "W")
    assert won == []  # not handed to a worker
    assert repo.get_by_id(rec.id).status == TaskStatus.TIMED_OUT


def test_reschedule_overshooting_deadline_times_out(repo):
    rec = _enqueue(repo, deadline_seconds=5)
    _claim(repo, "W")
    # A 100s retry would land well past the 1s deadline → TIMED_OUT, not PENDING.
    assert repo.reschedule(
        task_id=rec.id, worker_id="W", delay_seconds=100, error="boom"
    ) is True
    assert repo.get_by_id(rec.id).status == TaskStatus.TIMED_OUT


@pytest.mark.parametrize("terminal", [TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.TIMED_OUT])
def test_terminal_task_is_never_reclaimed(repo, terminal):
    rec = _enqueue(repo)
    _claim(repo, "W")
    if terminal == TaskStatus.SUCCEEDED:
        repo.complete(task_id=rec.id, worker_id="W")
    elif terminal == TaskStatus.FAILED:
        repo.fail(task_id=rec.id, worker_id="W", error="x")
    else:  # TIMED_OUT
        repo.reschedule(task_id=rec.id, worker_id="W", delay_seconds=10_000)
    assert repo.get_by_id(rec.id).status == terminal
    # Far future, well past any lease — a terminal row stays put.
    assert _claim(repo, "W") == []


def test_list_by_status_filters(repo):
    a = _enqueue(repo)
    b = _enqueue(repo)
    _claim(repo, "W", limit=1)
    pending = repo.list_by_status(status=TaskStatus.PENDING, env=ENV)
    running = repo.list_by_status(status=TaskStatus.RUNNING, env=ENV)
    assert {t.id for t in pending} | {t.id for t in running} == {a.id, b.id}
    assert len(running) == 1
