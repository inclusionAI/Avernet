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
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.repository.agent_attempt import (
    AgentAttemptRepository,
)
from agentclaw.community.core.agentswarm.repository.agent_attempt.impl import (
    EXECUTOR_STATE_DESTROYED, EXECUTOR_STATE_SPAWNED,
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