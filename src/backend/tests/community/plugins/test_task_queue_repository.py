"""Integration tests for TaskQueueRepository against real in-memory SQLite.

The same single ORM body runs on prod OceanBase, so the DB-side claim CAS,
lease reclaim, holder-guarded transitions, and deadline timeout are all
exercised against a real database here. Timing is DB-owned (no injected
``now``), so the few time-sensitive cases use short real sleeps.
"""
import re
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Side-effect import: registers TaskQueueModel on Base.metadata so
# create_all() builds the ac_task_queue table.
from agentclaw.community.core.task_queue.repository.models import TaskQueueModel  # noqa: F401
from agentclaw.community.core.task_queue.types import TaskStatus
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.plugins.task_queue_repository import (
    _ACTIVE_IDEM_INDEX,
    _MAX_IDEMPOTENCY_KEY_LEN,
    TaskQueueRepository,
    _is_active_idem_conflict,
)

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


def _enqueue_result(
    repo,
    *,
    payload=None,
    delay_seconds=0,
    deadline_seconds=3600,
    env=ENV,
    task_type="demo",
    idempotency_key=None,
):
    """The full ``EnqueueResult`` — for tests that care about ``created``."""
    return repo.enqueue(
        task_type=task_type,
        payload=payload if payload is not None else {"k": "v"},
        delay_seconds=delay_seconds,
        deadline_seconds=deadline_seconds,
        env=env,
        idempotency_key=idempotency_key,
    )


def _enqueue(repo, **kwargs):
    """Just the ``TaskRecord``. Keeps every pre-existing test that predates the
    idempotency key reading naturally, and makes those tests a regression guard
    for "un-keyed enqueue behaves exactly as before"."""
    return _enqueue_result(repo, **kwargs).record


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


def test_renew_lease_keeps_claim_alive_past_original_lease(repo):
    rec = _enqueue(repo)
    _claim(repo, "W", lease=1)
    assert repo.renew_lease(task_id=rec.id, worker_id="W", lease_seconds=60) is True
    time.sleep(2.1)  # past the original 1s lease
    # Renewed to 60s → another worker still cannot reclaim it.
    assert _claim(repo, "B", lease=60) == []
    stored = repo.get_by_id(rec.id)
    assert stored.status == TaskStatus.RUNNING and stored.claimed_by == "W"


def test_renew_lease_false_for_stale_worker(repo):
    rec = _enqueue(repo)
    _claim(repo, "A", lease=1)
    time.sleep(2.1)
    _claim(repo, "B", lease=60)  # B takes over
    assert repo.renew_lease(task_id=rec.id, worker_id="A", lease_seconds=60) is False
    stored = repo.get_by_id(rec.id)
    assert stored.status == TaskStatus.RUNNING and stored.claimed_by == "B"


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


# ── enqueue idempotency: dedup, opt-out, scoping ────────────────────────────
#
# The tests above this line all enqueue *without* a key, so they double as the
# regression guard for "un-keyed enqueue behaves exactly as it always has".


def _all_rows(repo):
    """Every row, read through the model so the enforcement column — which is
    deliberately not projected onto TaskRecord — can be asserted on."""
    with repo._db.orm_session() as db:
        return db.query(TaskQueueModel).all()


def _key_columns(repo, task_id):
    with repo._db.orm_session() as db:
        row = db.query(TaskQueueModel).filter(TaskQueueModel.id == task_id).first()
        return row.idempotency_key, row.active_idempotency_key


def test_unkeyed_enqueue_creates_a_row_with_both_key_columns_null(repo):
    res = _enqueue_result(repo)
    assert res.created is True
    assert res.record.idempotency_key is None
    assert _key_columns(repo, res.record.id) == (None, None)


def test_keyed_enqueue_writes_both_key_columns(repo):
    res = _enqueue_result(repo, idempotency_key="k1")
    assert res.created is True
    assert res.record.idempotency_key == "k1"
    # The enforcement copy mirrors the audit value while the task is live.
    assert _key_columns(repo, res.record.id) == ("k1", "k1")


def test_duplicate_keyed_enqueue_returns_existing_and_inserts_nothing(repo):
    first = _enqueue_result(repo, idempotency_key="k1")
    second = _enqueue_result(repo, idempotency_key="k1")

    assert (first.created, second.created) == (True, False)
    assert second.record.id == first.record.id
    assert len(_all_rows(repo)) == 1  # no second row


def test_multiple_null_keys_coexist(repo):
    """The relied-upon engine property that makes opt-in work: NULLs are
    distinct in a unique index, so un-keyed enqueues never collide."""
    ids = {_enqueue(repo).id for _ in range(3)}

    assert len(ids) == 3
    assert len(_all_rows(repo)) == 3


def test_same_key_under_different_task_type_does_not_collide(repo):
    a = _enqueue_result(repo, task_type="demo", idempotency_key="k1")
    b = _enqueue_result(repo, task_type="other", idempotency_key="k1")

    assert (a.created, b.created) == (True, True)
    assert a.record.id != b.record.id


def test_same_key_under_different_env_does_not_collide(repo):
    a = _enqueue_result(repo, env="dev", idempotency_key="k1")
    b = _enqueue_result(repo, env="prod", idempotency_key="k1")

    assert (a.created, b.created) == (True, True)
    assert a.record.id != b.record.id


# ── enqueue idempotency: key release across terminal transitions ────────────


def _drive_to_terminal(repo, task_id, how):
    """Take a task to each of the four terminal states the repository can
    produce. These are exactly the transitions that must release the key."""
    if how == "claim_deadline":
        # Retired at claim time for being past its deadline — never runs.
        assert _claim(repo, "W") == []
        return
    _claim(repo, "W")
    if how == "complete":
        assert repo.complete(task_id=task_id, worker_id="W")
    elif how == "fail":
        assert repo.fail(task_id=task_id, worker_id="W", error="boom")
    else:  # reschedule_overshoot — a retry that would land past the deadline
        assert repo.reschedule(task_id=task_id, worker_id="W", delay_seconds=10_000)


@pytest.mark.parametrize(
    "how, expected",
    [
        ("complete", TaskStatus.SUCCEEDED),
        ("fail", TaskStatus.FAILED),
        ("reschedule_overshoot", TaskStatus.TIMED_OUT),
        ("claim_deadline", TaskStatus.TIMED_OUT),
    ],
)
def test_terminal_transition_releases_key_and_allows_reenqueue(repo, how, expected):
    deadline = 0 if how == "claim_deadline" else 3600
    first = _enqueue_result(repo, idempotency_key="k1", deadline_seconds=deadline)

    _drive_to_terminal(repo, first.record.id, how)
    assert repo.get_by_id(first.record.id).status == expected

    # The enforcement column is cleared; the audit value survives.
    assert _key_columns(repo, first.record.id) == ("k1", None)

    # …so the same key may legitimately be enqueued again. This is the whole
    # point of active-only: retry, re-poll, and repeated restart depend on it.
    second = _enqueue_result(repo, idempotency_key="k1")
    assert second.created is True
    assert second.record.id != first.record.id


def test_live_task_retains_key_while_running(repo):
    first = _enqueue_result(repo, idempotency_key="k1")
    _claim(repo, "W")

    assert repo.get_by_id(first.record.id).status == TaskStatus.RUNNING
    assert _key_columns(repo, first.record.id) == ("k1", "k1")
    assert _enqueue_result(repo, idempotency_key="k1").created is False


def test_reschedule_back_to_pending_retains_key(repo):
    first = _enqueue_result(repo, idempotency_key="k1")
    _claim(repo, "W")
    assert repo.reschedule(task_id=first.record.id, worker_id="W", delay_seconds=1)

    assert repo.get_by_id(first.record.id).status == TaskStatus.PENDING
    # Still live → still holding the key.
    assert _key_columns(repo, first.record.id) == ("k1", "k1")
    assert _enqueue_result(repo, idempotency_key="k1").created is False


def test_past_deadline_task_not_yet_scanned_still_holds_its_key(repo):
    """Documents a known edge: past-deadline is not the same as terminal. Such
    a task still holds its key until a claim scan retires it, so a duplicate
    enqueue joins a task that will never run. It self-heals on the next scan."""
    first = _enqueue_result(repo, idempotency_key="k1", deadline_seconds=0)

    joined = _enqueue_result(repo, idempotency_key="k1")
    assert joined.created is False
    assert joined.record.id == first.record.id

    _claim(repo, "W")  # the scan retires it and frees the key
    assert repo.get_by_id(first.record.id).status == TaskStatus.TIMED_OUT
    assert _enqueue_result(repo, idempotency_key="k1").created is True


# ── enqueue idempotency: conflict scoping and the insert/re-SELECT race ─────


def test_is_active_idem_conflict_recognises_both_engine_message_forms():
    """The one genuinely engine-specific line in an otherwise unified body, so
    it is a pure function over the exception and testable without MySQL."""
    def err(message):
        return IntegrityError("INSERT INTO ac_task_queue ...", {}, Exception(message))

    # MySQL / OceanBase name the index.
    assert _is_active_idem_conflict(
        err("(1062, \"Duplicate entry 'dev-demo-k1' for key "
            "'uk_env_task_type_active_idem'\")")
    )
    # SQLite names the columns.
    assert _is_active_idem_conflict(
        err("UNIQUE constraint failed: ac_task_queue.env, ac_task_queue.task_type, "
            "ac_task_queue.active_idempotency_key")
    )
    # Anything else must not be read as a duplicate enqueue.
    assert not _is_active_idem_conflict(
        err("UNIQUE constraint failed: ac_task_queue.some_other_column")
    )
    assert not _is_active_idem_conflict(err("NOT NULL constraint failed: x.y"))


def test_unrelated_integrity_error_propagates(repo, monkeypatch):
    """A blanket except would turn someone else's constraint violation into a
    bogus duplicate and hand back the wrong row."""
    def boom(**_kwargs):
        raise IntegrityError(
            "INSERT ...", {}, Exception("UNIQUE constraint failed: ac_task_queue.other")
        )

    monkeypatch.setattr(repo, "_insert", boom)
    with pytest.raises(IntegrityError):
        _enqueue_result(repo, idempotency_key="k1")


def test_repo_is_usable_after_a_caught_integrity_error(repo):
    """The failed INSERT must leave nothing to clean up — orm_session() rolls
    back and closes, so the re-SELECT and every later call get a fresh session."""
    _enqueue_result(repo, idempotency_key="k1")
    assert _enqueue_result(repo, idempotency_key="k1").created is False  # conflict

    # Reads and writes both still work afterwards.
    assert len(repo.list_by_status(status=TaskStatus.PENDING, env=ENV)) == 1
    assert _enqueue_result(repo, idempotency_key="k2").created is True
    assert _enqueue(repo).id is not None


def test_holder_going_terminal_between_insert_and_lookup_is_retried(repo):
    """The insert/re-SELECT race: the conflicting row can reach a terminal state
    in that window, releasing the key. The retry then inserts cleanly."""
    first = _enqueue_result(repo, idempotency_key="k1")
    real_find = repo._find_active_by_key

    def free_the_key_then_look(**kwargs):
        # Simulate the holder finishing the instant after our INSERT lost.
        _claim(repo, "W")
        repo.complete(task_id=first.record.id, worker_id="W")
        repo._find_active_by_key = real_find
        return real_find(**kwargs)

    repo._find_active_by_key = free_the_key_then_look

    second = _enqueue_result(repo, idempotency_key="k1")
    assert second.created is True  # second attempt inserted
    assert second.record.id != first.record.id
    assert repo.get_by_id(first.record.id).status == TaskStatus.SUCCEEDED


def test_enqueue_raises_when_it_can_neither_insert_nor_resolve_a_holder(repo):
    """Two consecutive losses surface rather than looping forever."""
    _enqueue_result(repo, idempotency_key="k1")
    # A holder that is never found: every attempt conflicts, every lookup misses.
    repo._find_active_by_key = lambda **_kwargs: None

    with pytest.raises(RuntimeError, match="could not insert or resolve a holder"):
        _enqueue_result(repo, idempotency_key="k1")


# ── enqueue idempotency: key validation ─────────────────────────────────────
#
# The bound is enforced in Python because the engines disagree and SQLite —
# which every test here runs on — cannot observe the disagreement.


def test_max_key_length_tracks_the_column_width():
    """The constant is read off the column, so schema and check cannot drift."""
    assert _MAX_IDEMPOTENCY_KEY_LEN == TaskQueueModel.__table__.c.idempotency_key.type.length
    assert (
        TaskQueueModel.__table__.c.active_idempotency_key.type.length
        == _MAX_IDEMPOTENCY_KEY_LEN
    )


def test_key_at_the_length_limit_is_accepted(repo):
    res = _enqueue_result(repo, idempotency_key="k" * _MAX_IDEMPOTENCY_KEY_LEN)
    assert res.created is True
    assert len(res.record.idempotency_key) == _MAX_IDEMPOTENCY_KEY_LEN


def test_over_length_key_is_rejected_before_any_row_is_inserted(repo):
    """SQLite would happily store the overflow and dedup exactly; a non-strict
    MySQL would truncate and collide two distinct keys, handing the caller
    somebody else's task. Reject in Python so both engines agree."""
    too_long = "k" * (_MAX_IDEMPOTENCY_KEY_LEN + 1)

    with pytest.raises(ValueError, match="exceeds"):
        _enqueue_result(repo, idempotency_key=too_long)

    assert _all_rows(repo) == []  # nothing inserted


def test_keys_differing_only_past_the_limit_cannot_both_be_enqueued(repo):
    """The concrete collision the bound prevents: two keys with a shared
    190-char prefix would truncate to the same stored value under non-strict
    MySQL, so the second caller would silently join the first one's task."""
    prefix = "publish:" + "a" * (_MAX_IDEMPOTENCY_KEY_LEN - 8)
    assert len(prefix) == _MAX_IDEMPOTENCY_KEY_LEN

    for suffix in (":online", ":offline"):
        with pytest.raises(ValueError, match="exceeds"):
            _enqueue_result(repo, idempotency_key=prefix + suffix)


@pytest.mark.parametrize("blank", ["", " ", "\t", "\n  "])
def test_blank_key_is_rejected_rather_than_becoming_a_global_dedup_slot(repo, blank):
    """``None`` is the opt-out. If "" were a valid key it would be one shared
    dedup slot per (env, task_type), collapsing unrelated work onto one row."""
    with pytest.raises(ValueError, match="non-empty"):
        _enqueue_result(repo, idempotency_key=blank)

    assert _all_rows(repo) == []


def test_key_is_stored_verbatim_without_stripping(repo):
    """Validation never rewrites an accepted key — silently mutating it would be
    the same class of bug as truncating it. Internal spacing survives exactly;
    only the *ends* are constrained (see the PAD SPACE test below)."""
    res = _enqueue_result(repo, idempotency_key="publish:a b:poll")
    assert res.record.idempotency_key == "publish:a b:poll"
    assert _key_columns(repo, res.record.id) == ("publish:a b:poll", "publish:a b:poll")


def test_validation_also_applies_through_the_service_facade(repo):
    """Adopters call TaskQueueService, so the guard must hold on that path too;
    it delegates to the repository, which is where the check lives."""
    service = TaskQueueService(repo)
    with pytest.raises(ValueError, match="exceeds"):
        service.enqueue(
            "demo", {}, 3600, idempotency_key="k" * (_MAX_IDEMPOTENCY_KEY_LEN + 1)
        )
    with pytest.raises(ValueError, match="non-empty"):
        service.enqueue("demo", {}, 3600, idempotency_key="")
    assert _all_rows(repo) == []


# ── enqueue idempotency: key comparison is byte-for-byte ────────────────────


def test_key_columns_pin_binary_collation_on_mysql():
    """Keys are compared byte-for-byte, but MySQL/OceanBase default to a
    ``utf8mb4_*_ci`` collation under which 'publish:Bot-A:poll' and
    'publish:bot-a:poll' would be the SAME key in the unique index. SQLite
    already compares BINARY, so no behavioural test here can catch a regression
    — assert the rendered MySQL DDL directly instead.

    ``utf8mb4_bin`` closes case folding but *not* PAD SPACE; trailing-space
    collisions are handled by rejecting such keys at validation instead (see
    ``test_keys_with_surrounding_whitespace_are_rejected``)."""
    from sqlalchemy.dialects import mysql
    from sqlalchemy.schema import CreateTable

    ddl = str(CreateTable(TaskQueueModel.__table__).compile(dialect=mysql.dialect()))
    # task_type is in the same unique index, and an index is only as precise as
    # its least precise column — leaving it _ci would reopen the hole one column
    # over, with 'Job' and 'job' as a single dedup slot.
    for column in ("idempotency_key", "active_idempotency_key", "task_type"):
        line = next(ln for ln in ddl.splitlines() if ln.strip().startswith(column))
        assert "COLLATE utf8mb4_bin" in line, f"{column} lost its binary collation: {line}"


def test_env_deliberately_keeps_the_default_collation():
    """The counterpart to the test above: env is scoped by the same index but is
    intentionally NOT pinned, because it is also compared by the claim/reclaim
    eligibility filter and carries two other indexes, so altering it would
    change pre-existing behaviour. Pinned as a decision, so flipping it is a
    deliberate act rather than a drive-by consistency edit."""
    from sqlalchemy.dialects import mysql
    from sqlalchemy.schema import CreateTable

    ddl = str(CreateTable(TaskQueueModel.__table__).compile(dialect=mysql.dialect()))
    line = next(ln for ln in ddl.splitlines() if ln.strip().startswith("env "))
    assert "COLLATE" not in line, f"env collation changed deliberately? {line}"


def test_case_variants_are_distinct_keys(repo):
    """What ``utf8mb4_bin`` buys: under the default ``_ci`` collation these two
    would be the same key in the unique index. Asserted on SQLite (BINARY
    natively) so the intent is recorded, even though only the DDL test above
    can catch a MySQL-side regression."""
    a = _enqueue_result(repo, idempotency_key="publish:Bot-A:poll")
    b = _enqueue_result(repo, idempotency_key="publish:bot-a:poll")
    assert (a.created, b.created) == (True, True)
    assert a.record.id != b.record.id


@pytest.mark.parametrize("key", ["k1 ", " k1", " k1 ", "k1\t", "\nk1"])
def test_keys_with_surrounding_whitespace_are_rejected(repo, key):
    """What ``utf8mb4_bin`` does *not* buy: it is PAD SPACE, so 'k1' and 'k1 '
    still compare equal in the unique index on MySQL/OceanBase while staying
    distinct here. The collation alone cannot close this.

    Rejecting keys whose ends could be padded away closes it independently of
    the engine's pad attribute: if no accepted key carries trailing whitespace,
    PAD SPACE can never merge two accepted keys. Asserting *rejection* is the
    point — the distinctness test this replaced passed on SQLite while encoding
    a guarantee production does not honour."""
    _enqueue_result(repo, idempotency_key="k1")  # the bare form is fine

    with pytest.raises(ValueError, match="leading or trailing whitespace"):
        _enqueue_result(repo, idempotency_key=key)

    assert len(_all_rows(repo)) == 1  # nothing extra inserted


# ── checked-in migrations ───────────────────────────────────────────────────

_SQL_DIR = (
    Path(__file__).resolve().parents[3]
    / "src/agentclaw/community/core/task_queue/sql"
)
_MIGRATION_STEM = "2026_08_04_task_queue_idempotency"


def _pre_migration_table():
    """``ac_task_queue`` as it looked before this change: the two key columns
    and the dedup index removed, everything else identical to the ORM."""
    live = TaskQueueModel.__table__
    pre = live.to_metadata(MetaData())
    for column in ("idempotency_key", "active_idempotency_key"):
        pre._columns.remove(pre.c[column])
    pre.indexes = {i for i in pre.indexes if i.name != _ACTIVE_IDEM_INDEX}
    return pre


def _statements(path):
    """Split a migration file into executable statements, dropping comments."""
    body = re.sub(r"(?m)^--.*$", "", path.read_text())
    return [s.strip() for s in body.split(";") if s.strip()]


def test_checked_in_sqlite_migration_upgrades_a_pre_migration_table(tmp_path):
    """The community profile never runs ``create_all`` — an operator applies
    these files by hand — so a broken one is only discovered in their
    production. Build the *old* table, run the checked-in SQLite migration
    against it, then drive real keyed enqueues through the repository: proof the
    file actually lands the schema the ORM requires, not just that it parses."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _pre_migration_table().create(engine)
    assert "idempotency_key" not in {
        c["name"] for c in inspect(engine).get_columns("ac_task_queue")
    }

    with engine.begin() as conn:
        for statement in _statements(_SQL_DIR / f"{_MIGRATION_STEM}.sqlite.sql"):
            conn.execute(text(statement))

    repo = TaskQueueRepository(InMemorySqliteDB(engine))
    first = _enqueue_result(repo, idempotency_key="k1")
    second = _enqueue_result(repo, idempotency_key="k1")
    assert first.created is True and second.created is False
    assert second.record.id == first.record.id  # dedup, via the migrated index

    # And the opt-out still works, which is what the index's NULL handling buys.
    assert _enqueue_result(repo).created is True
    assert _enqueue_result(repo).created is True


def test_every_dialect_migration_agrees_on_the_names_it_creates():
    """Four files, one migration. A rename in one and not the others would leave
    some store's index named differently from ``_ACTIVE_IDEM_INDEX``, which is
    how the repository recognises a duplicate-key error."""
    variants = ["sql", "mysql.sql", "postgres.sql", "sqlite.sql"]
    for variant in variants:
        path = _SQL_DIR / f"{_MIGRATION_STEM}.{variant}"
        body = path.read_text()
        for name in ("idempotency_key", "active_idempotency_key", _ACTIVE_IDEM_INDEX):
            assert name in body, f"{path.name} never mentions {name}"


def test_only_the_mysql_family_migrations_carry_a_collation():
    """SQLite compares BINARY and PostgreSQL varchar equality is exact under a
    deterministic collation, so those two need no COLLATE — and must not carry
    one, since neither engine would accept ``utf8mb4_bin``. The MySQL-family
    files must, because their server default would merge case variants."""
    for variant in ("sql", "mysql.sql"):
        body = (_SQL_DIR / f"{_MIGRATION_STEM}.{variant}").read_text()
        assert body.count("COLLATE utf8mb4_bin") == 3, (
            f"{variant}: expected both key columns plus task_type to pin the collation"
        )
    for variant in ("postgres.sql", "sqlite.sql"):
        body = (_SQL_DIR / f"{_MIGRATION_STEM}.{variant}").read_text()
        executable = re.sub(r"(?m)^--.*$", "", body)
        assert "COLLATE" not in executable.upper(), (
            f"{variant} carries a collation clause its engine cannot honour"
        )
