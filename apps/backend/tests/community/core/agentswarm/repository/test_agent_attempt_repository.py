"""AgentAttemptRepository ledger tests (D-K/D-L): fence, converge, list, mark.

Every scenario runs on a real SQLite engine (StaticPool, registry ALL_MODELS
create_all) with the same JobRepository claim ladder the runtime uses, so the
fence behavior is exercised against the actual uk_job_run/claim_epoch table
shape instead of a double.

Conventions follow test_slice_store.py: hand-written helpers, no MagicMock.
"""
from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import Update, create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.repository.agent_attempt import (
    AgentAttemptRepository,
)
from agentclaw.community.core.agentswarm.repository.agent_attempt.impl import (
    EXECUTOR_STATE_BOUND, EXECUTOR_STATE_DESTROYED, EXECUTOR_STATE_SPAWNED,
)
from agentclaw.community.core.agentswarm.repository.job import JobRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository

TENANT = dm.TenantId("tenant-a")
WORKER = "ledger-worker-1"
RECLAIMER = "ledger-reclaimer-1"


class _TestDB:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine)

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
    yield _TestDB(engine)
    engine.dispose()


@pytest.fixture()
def run(db) -> dm.Run:
    run = dm.Run(
        run_id=dm.RunId(uuid.uuid4().hex),
        tenant=TENANT,
        goal="ledger goal",
        agent_catalog=(),
        provider=dm.ProviderKind("stub"),
        request_metadata=dm.RequestMetadata(),
        status=dm.RunStatus.RUNNING,
        started_at=dm.EpochMillis(int(time.time() * 1000)),
    )
    RunRepository(db).create(run)
    return run


def _claim(db, record: dm.Run) -> int:
    job_repo = JobRepository(db)
    job_repo.enqueue(run_id=record.run_id, tenant=TENANT, deadline_seconds=3600)
    return job_repo.claim_batch(WORKER, [TENANT], 1, 0)[0]["claim_epoch"]


def _reclaim(db, record: dm.Run) -> int:
    time.sleep(0.02)
    return JobRepository(db).reclaim_batch(RECLAIMER, 1, 60)[0]["claim_epoch"]


def _record(db, run_, agent_id="agent-1", claim_epoch=1, attempt=1,
            kind=dm.AgentKind.BOT.value, executor_id=None, executor_name="ac-x"):
    repo = AgentAttemptRepository(db)
    return repo.record_agent_binding(
        run_.run_id, TENANT, dm.AgentId(agent_id), claim_epoch, attempt, kind,
        executor_id or f"ac_{uuid.uuid4().hex[:8]}", executor_name,
    )


class TestRecordFence:
    def test_no_job_row_fences_out_inert(self, db, run):
        # No job row: there is no live claim to fence against, so the
        # journal write must be inert — nothing lands, False returns.
        assert _record(db, run, claim_epoch=0) is False
        assert AgentAttemptRepository(db).list_agent_bindings(run.run_id, TENANT) == []

    def test_matching_epoch_journals_spawned_row(self, db, run):
        epoch = _claim(db, run)
        assert _record(db, run, claim_epoch=epoch, attempt=3,
                        executor_id="ac_personaid", executor_name="scout-ab12cd34") is True
        (row,) = AgentAttemptRepository(db).list_agent_bindings(run.run_id, TENANT)
        assert row == {
            "agent_id": "agent-1",
            "claim_epoch": epoch,
            "attempt": 3,
            "executor_kind": dm.AgentKind.BOT.value,
            "executor_id": "ac_personaid",
            "executor_name": "scout-ab12cd34",
            "executor_state": EXECUTOR_STATE_SPAWNED,
            "started_at": row["started_at"],
            "result_status": None,
            "result_message": None,
        }
        assert row["started_at"] is not None

    def test_stale_epoch_write_is_inert(self, db, run):
        lost_epoch = _claim(db, run)
        _reclaim(db, run)
        assert _record(db, run, claim_epoch=lost_epoch) is False
        assert AgentAttemptRepository(db).list_agent_bindings(run.run_id, TENANT) == []

    def test_current_epoch_writes_after_reclaim(self, db, run):
        _claim(db, run)
        fresh_epoch = _reclaim(db, run)
        assert _record(db, run, claim_epoch=fresh_epoch) is True
        (row,) = AgentAttemptRepository(db).list_agent_bindings(run.run_id, TENANT)
        assert row["claim_epoch"] == fresh_epoch


class TestReplayConvergence:
    def test_same_key_replay_converges_on_the_landed_row(self, db, run):
        epoch = _claim(db, run)
        assert _record(db, run, claim_epoch=epoch, attempt=2,
                        executor_id="ac_same", executor_name="same") is True
        assert _record(db, run, claim_epoch=epoch, attempt=2,
                        executor_id="ac_same", executor_name="same") is True
        rows = AgentAttemptRepository(db).list_agent_bindings(run.run_id, TENANT)
        assert len(rows) == 1

    def test_new_attempt_same_epoch_is_a_new_row(self, db, run):
        epoch = _claim(db, run)
        _record(db, run, claim_epoch=epoch, attempt=1, executor_id="ac_a1")
        _record(db, run, claim_epoch=epoch, attempt=2, executor_id="ac_a2")
        rows = AgentAttemptRepository(db).list_agent_bindings(run.run_id, TENANT)
        assert [r["executor_id"] for r in rows] == ["ac_a1", "ac_a2"]
        assert sorted(r["attempt"] for r in rows) == [1, 2]


class TestListShape:
    def test_cross_slice_replay_keeps_both_rows_for_one_executor(self, db, run):
        # The adopt shape: a crashed slice's same-attempt persona is
        # re-derived by the reclaimer and journaled under the new epoch —
        # one executor id, two ledger rows, both addressable (D-K).
        first = _claim(db, run)
        _record(db, run, claim_epoch=first, attempt=1, executor_id="ac_adopted")
        second = _reclaim(db, run)
        _record(db, run, claim_epoch=second, attempt=1, executor_id="ac_adopted")
        (one, two) = AgentAttemptRepository(db).list_agent_bindings(run.run_id, TENANT)
        assert one["executor_id"] == two["executor_id"] == "ac_adopted"
        assert {one["claim_epoch"], two["claim_epoch"]} == {first, second}

    def test_listing_is_tenant_scoped(self, db, run):
        epoch = _claim(db, run)
        assert _record(db, run, claim_epoch=epoch) is True
        other = AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, dm.TenantId("tenant-b"))
        assert other == []

    def test_llm_kind_rows_are_journaled_like_bot_rows(self, db, run):
        epoch = _claim(db, run)
        assert _record(db, run, claim_epoch=epoch, agent_id="agent-llm",
                        kind=dm.AgentKind.LLM.value,
                        executor_id="0123456789abcdef") is True
        (row,) = AgentAttemptRepository(db).list_agent_bindings(run.run_id, TENANT)
        assert row["executor_kind"] == dm.AgentKind.LLM.value
        assert row["executor_state"] == EXECUTOR_STATE_SPAWNED


class TestMarkDestroyed:
    def test_marks_every_row_of_one_executor(self, db, run):
        epoch = _claim(db, run)
        _record(db, run, claim_epoch=epoch, attempt=1, executor_id="ac_adopted")
        later = _reclaim(db, run)
        _record(db, run, claim_epoch=later, attempt=1, executor_id="ac_adopted")
        _record(db, run, claim_epoch=later, attempt=2, executor_id="ac_other")

        repo = AgentAttemptRepository(db)
        assert repo.mark_binding_destroyed(run.run_id, TENANT, "ac_adopted") == 2
        by_id = {r["executor_id"]: r for r in repo.list_agent_bindings(run.run_id, TENANT)}
        assert by_id["ac_adopted"]["executor_state"] == EXECUTOR_STATE_DESTROYED
        assert by_id["ac_other"]["executor_state"] == EXECUTOR_STATE_SPAWNED

    def test_already_destroyed_rows_are_not_recounted(self, db, run):
        epoch = _claim(db, run)
        _record(db, run, claim_epoch=epoch, executor_id="ac_once")
        repo = AgentAttemptRepository(db)
        assert repo.mark_binding_destroyed(run.run_id, TENANT, "ac_once") == 1
        assert repo.mark_binding_destroyed(run.run_id, TENANT, "ac_once") == 0

    def test_unknown_executor_marks_nothing(self, db, run):
        epoch = _claim(db, run)
        _record(db, run, claim_epoch=epoch)
        assert AgentAttemptRepository(db).mark_binding_destroyed(
            run.run_id, TENANT, "ac_ghost") == 0


class TestRecordAttemptResult:
    """``record_attempt_result``: the fenced per-attempt outcome UPDATE."""

    def _result(self, db, run_, result_status="COMPLETED",
                result_message=None, agent_id="agent-1", claim_epoch=1,
                attempt=1):
        repo = AgentAttemptRepository(db)
        rowcount = repo.record_attempt_result(
            run_.run_id, TENANT, dm.AgentId(agent_id), claim_epoch, attempt,
            result_status, result_message,
        )
        return rowcount, repo

    def test_matching_epoch_updates_result_columns(self, db, run):
        epoch = _claim(db, run)
        _record(db, run, claim_epoch=epoch, attempt=1)
        rowcount, repo = self._result(
            db, run, result_status="FAILED",
            result_message="quota exceeded", claim_epoch=epoch, attempt=1)
        assert rowcount == 1
        (row,) = repo.list_agent_bindings(run.run_id, TENANT)
        assert row["result_status"] == "FAILED"
        assert row["result_message"] == "quota exceeded"

    def test_completed_result_message_is_stored_as_null(self, db, run):
        epoch = _claim(db, run)
        _record(db, run, claim_epoch=epoch, attempt=1)
        rowcount, repo = self._result(
            db, run, result_status="COMPLETED", result_message=None,
            claim_epoch=epoch, attempt=1)
        assert rowcount == 1
        (row,) = repo.list_agent_bindings(run.run_id, TENANT)
        assert row["result_status"] == "COMPLETED"
        assert row["result_message"] is None

    def test_stale_epoch_write_returns_zero_and_leaves_row_unchanged(self, db, run):
        lost_epoch = _claim(db, run)
        _record(db, run, claim_epoch=lost_epoch, attempt=1)
        _reclaim(db, run)
        rowcount, repo = self._result(
            db, run, result_status="FAILED", result_message="late",
            claim_epoch=lost_epoch, attempt=1)
        assert rowcount == 0
        (row,) = repo.list_agent_bindings(run.run_id, TENANT)
        assert row["result_status"] is None
        assert row["result_message"] is None

    def test_destroyed_row_is_never_updated(self, db, run):
        epoch = _claim(db, run)
        _record(db, run, claim_epoch=epoch, attempt=1, executor_id="ac_dead")
        repo = AgentAttemptRepository(db)
        assert repo.mark_binding_destroyed(run.run_id, TENANT, "ac_dead") == 1
        rowcount = repo.record_attempt_result(
            run.run_id, TENANT, dm.AgentId("agent-1"), epoch, 1,
            "FAILED", "after destroy")
        assert rowcount == 0
        (row,) = repo.list_agent_bindings(run.run_id, TENANT)
        assert row["executor_state"] == EXECUTOR_STATE_DESTROYED
        assert row["result_status"] is None

    def test_unknown_attempt_or_agent_returns_zero(self, db, run):
        epoch = _claim(db, run)
        _record(db, run, claim_epoch=epoch, attempt=1)
        repo = AgentAttemptRepository(db)
        assert repo.record_attempt_result(
            run.run_id, TENANT, dm.AgentId("agent-1"), epoch, 99,
            "FAILED", "no such attempt") == 0
        assert repo.record_attempt_result(
            run.run_id, TENANT, dm.AgentId("agent-ghost"), epoch, 1,
            "FAILED", "no such agent") == 0
        (row,) = repo.list_agent_bindings(run.run_id, TENANT)
        assert row["result_status"] is None


class TestWriteAheadIntent:
    """``record_agent_attempt_intent``: the pre-setup ledger leg."""

    def _intent(self, db, run_, agent_id="agent-1", claim_epoch=1, attempt=1,
                kind=dm.AgentKind.BOT.value, executor_id="",
                executor_name="scout"):
        repo = AgentAttemptRepository(db)
        journaled = repo.record_agent_attempt_intent(
            run_.run_id, TENANT, dm.AgentId(agent_id), claim_epoch, attempt,
            kind, executor_id, executor_name,
        )
        return journaled, repo

    def test_no_job_row_fences_intent_out_inert(self, db, run):
        journaled, repo = self._intent(db, run, claim_epoch=0)
        assert journaled is False
        assert repo.list_agent_bindings(run.run_id, TENANT) == []

    def test_matching_epoch_journals_blank_spawned_intent(self, db, run):
        epoch = _claim(db, run)
        journaled, repo = self._intent(db, run, claim_epoch=epoch)
        assert journaled is True
        (row,) = repo.list_agent_bindings(run.run_id, TENANT)
        assert row == {
            "agent_id": "agent-1",
            "claim_epoch": epoch,
            "attempt": 1,
            "executor_kind": dm.AgentKind.BOT.value,
            "executor_id": "",
            "executor_name": "scout",
            "executor_state": EXECUTOR_STATE_SPAWNED,
            "started_at": row["started_at"],
            "result_status": None,
            "result_message": None,
        }

    def test_intent_replay_converges_on_the_landed_row(self, db, run):
        epoch = _claim(db, run)
        assert self._intent(db, run, claim_epoch=epoch)[0] is True
        assert self._intent(db, run, claim_epoch=epoch)[0] is True
        assert len(AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, TENANT)) == 1

    def test_binding_settles_the_intent_row_to_bound(self, db, run):
        epoch = _claim(db, run)
        self._intent(db, run, claim_epoch=epoch, attempt=2)
        assert _record(db, run, claim_epoch=epoch, attempt=2,
                       executor_id="ac_real", executor_name="scout-real") is True
        (row,) = AgentAttemptRepository(db).list_agent_bindings(run.run_id, TENANT)
        assert row["executor_id"] == "ac_real"
        assert row["executor_state"] == EXECUTOR_STATE_BOUND

    def test_fenced_settle_returns_false_without_touching_the_row(self, db, run):
        epoch = _claim(db, run)
        self._intent(db, run, claim_epoch=epoch)
        stale = _reclaim(db, run)
        assert stale != epoch
        # The settle carries the ROW's (now dead) epoch: the job row's
        # CURRENT claim_epoch is the reclaimer's, so the fence rejects
        # the UPDATE and the blank intent row stays untouched.
        assert _record(db, run, claim_epoch=epoch, attempt=1,
                       executor_id="ac_wrong") is False
        (row,) = AgentAttemptRepository(db).list_agent_bindings(run.run_id, TENANT)
        assert row["executor_id"] == ""
        assert row["executor_state"] == EXECUTOR_STATE_SPAWNED

    def test_destroyed_intent_row_is_never_resurrected(self, db, run):
        epoch = _claim(db, run)
        self._intent(db, run, claim_epoch=epoch, attempt=1)
        repo = AgentAttemptRepository(db)
        assert repo.mark_intent_binding_destroyed(
            run.run_id, TENANT, dm.AgentId("agent-1"), 1) == 1
        assert _record(db, run, claim_epoch=epoch, attempt=1,
                       executor_id="ac_late") is False
        (row,) = repo.list_agent_bindings(run.run_id, TENANT)
        assert row["executor_state"] == EXECUTOR_STATE_DESTROYED


class TestMarkIntentDestroyed:
    """Blank-id intent rows die by (agent, attempt) correlation."""

    def test_marks_only_blank_rows_of_the_attempt(self, db, run):
        epoch = _claim(db, run)
        repo = AgentAttemptRepository(db)
        repo.record_agent_attempt_intent(
            run.run_id, TENANT, dm.AgentId("agent-1"), epoch, 1,
            dm.AgentKind.BOT.value, "", "scout")
        _record(db, run, agent_id="agent-1", claim_epoch=epoch, attempt=2,
                executor_id="ac_settled")

        assert repo.mark_intent_binding_destroyed(
            run.run_id, TENANT, dm.AgentId("agent-1"), 1) == 1
        by_attempt = {r["attempt"]: r
                      for r in repo.list_agent_bindings(run.run_id, TENANT)}
        assert by_attempt[1]["executor_state"] == EXECUTOR_STATE_DESTROYED
        assert by_attempt[2]["executor_state"] == EXECUTOR_STATE_SPAWNED

    def test_marks_blank_rows_across_claim_epochs(self, db, run):
        # A crash-resume replays the same attempt number under a new
        # epoch; every blank row of that attempt shares the one derived
        # persona, so one destroy marks them all.
        first = _claim(db, run)
        repo = AgentAttemptRepository(db)
        repo.record_agent_attempt_intent(
            run.run_id, TENANT, dm.AgentId("agent-1"), first, 1,
            dm.AgentKind.BOT.value, "", "scout")
        second = _reclaim(db, run)
        repo.record_agent_attempt_intent(
            run.run_id, TENANT, dm.AgentId("agent-1"), second, 1,
            dm.AgentKind.BOT.value, "", "scout")

        assert repo.mark_intent_binding_destroyed(
            run.run_id, TENANT, dm.AgentId("agent-1"), 1) == 2
        assert all(
            r["executor_state"] == EXECUTOR_STATE_DESTROYED
            for r in repo.list_agent_bindings(run.run_id, TENANT)
        )

    def test_unknown_attempt_marks_nothing(self, db, run):
        epoch = _claim(db, run)
        _record(db, run, claim_epoch=epoch)  # settled row, not blank
        assert AgentAttemptRepository(db).mark_intent_binding_destroyed(
            run.run_id, TENANT, dm.AgentId("agent-1"), 1) == 0


def _fail_settle_update(db, code: int, message: str, times: int = 1) -> dict:
    """Patch ``db.orm_session`` so the settlement UPDATE raises a
    driver-shaped ``OperationalError`` on its first ``times`` executions
    and lets everything else through — the pymysql/mysqldb convention
    puts the MariaDB error code in ``orig.args[0]``. Only ``Update``
    statements raise: the binding replay still runs its gate SELECT and
    UK-violating INSERT against the real engine first, so the failure
    lands on exactly the settle's fenced UPDATE like the live incident.
    """
    original = db.orm_session
    state = {"raised": 0}

    @contextmanager
    def orm_session():
        with original() as session:
            native_execute = session.execute

            def execute(statement, *args, **kwargs):
                if (isinstance(statement, Update)
                        and state["raised"] < times):
                    state["raised"] += 1
                    raise OperationalError(
                        "UPDATE ac_agent_swarm_agent_attempt", {},
                        Exception(code, message),
                    )
                return native_execute(statement, *args, **kwargs)

            session.execute = execute
            yield session

    db.orm_session = orm_session
    return state


class TestSettleTransientRetry:
    """A transient lock fault on the settle UPDATE is a bounded retry,
    not a lost claim: the live MariaDB 1213 misread stamped a healthy
    in-flight attempt FAILED/slice_lost — the poison-pill root cause."""

    def _land_intent(self, db, run_, epoch, attempt=1) -> None:
        repo = AgentAttemptRepository(db)
        assert repo.record_agent_attempt_intent(
            run_.run_id, TENANT, dm.AgentId("agent-1"), epoch, attempt,
            dm.AgentKind.BOT.value, "", "scout",
        ) is True

    def _replay_binding(self, db, run_, epoch) -> bool:
        # Binding replay of the SAME (agent, epoch, attempt): the UK hit
        # converges through the fenced settlement — the live [collide]
        # path where the deadlock struck.
        return AgentAttemptRepository(db).record_agent_binding(
            run_.run_id, TENANT, dm.AgentId("agent-1"), epoch, 1,
            dm.AgentKind.BOT.value, "ac_personaid", "scout-ab12cd34",
        )

    def test_transient_deadlock_retried_and_settle_converges(self, db, run):
        epoch = _claim(db, run)
        self._land_intent(db, run, epoch)
        _fail_settle_update(
            db, 1213, "Deadlock found when trying to get lock", times=1)

        assert self._replay_binding(db, run, epoch) is True
        (row,) = AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, TENANT)
        assert row["executor_id"] == "ac_personaid"
        assert row["executor_state"] == EXECUTOR_STATE_BOUND

    def test_persistent_deadlock_exhausts_bounded_retries(self, db, run):
        epoch = _claim(db, run)
        self._land_intent(db, run, epoch)
        probe = _fail_settle_update(
            db, 1205,
            "Lock wait timeout exceeded; try restarting transaction",
            times=99)

        assert self._replay_binding(db, run, epoch) is False
        # Bounded, never an infinite retry against a wedged lock chain.
        assert probe["raised"] == 3
        (row,) = AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, TENANT)
        assert row["executor_id"] == ""
        assert row["executor_state"] == EXECUTOR_STATE_SPAWNED

    def test_non_transient_error_returns_false_without_retry(self, db, run):
        epoch = _claim(db, run)
        self._land_intent(db, run, epoch)
        probe = _fail_settle_update(
            db, 1050, "unknown ledger table", times=1)

        assert self._replay_binding(db, run, epoch) is False
        # Not lock-shaped: the settle must not burn its retries on it.
        assert probe["raised"] == 1
        (row,) = AgentAttemptRepository(db).list_agent_bindings(
            run.run_id, TENANT)
        assert row["executor_id"] == ""
        assert row["executor_state"] == EXECUTOR_STATE_SPAWNED