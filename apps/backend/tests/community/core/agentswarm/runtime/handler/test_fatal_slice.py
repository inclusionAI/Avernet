"""Fatal-outcome slice handling (uncovered-branch audit): the
orchestrator's unhandled-decision-type fallthrough is the in-model
producer of outcome["fatal"]; it must flow through
SwarmRunHandler._finalize_failure — terminal FAILED run row, durable dispatch
failed with the fatal text, retention applied (13.6 degradation) — while
a fenced (stale-epoch) fatal unwinds as a lost slice and a fenced
force_fail touches neither run nor dispatch (D28 claim fencing).
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository import registry as om

from tests.community.core.agentswarm.runtime.handler.test_swarm_slice import (
    TENANT, WORKER, _TestDB, FakeFactory, RepeatingLLMProvider, _build_env,
    _enqueue_and_claim, _mk_run, _spawn_decision,
)

FATAL_TEXT = "unhandled decision type: _RogueDecision"


@pytest.fixture()
def db():
    # StaticPool: the slice path offloads repo calls to to_thread workers.
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
    yield _TestDB(engine)
    engine.dispose()


def _dispatch_row(db, run_id):
    with db.orm_session() as session:
        row = session.execute(
            select(om.AcAgentSwarmRunDispatchModel).where(
                om.AcAgentSwarmRunDispatchModel.run_id == run_id,
            )
        ).scalar_one()
        return {
            "status": row.status,
            "last_error": row.last_error,
            "claimed_by": row.claimed_by,
            "claim_epoch": row.claim_epoch,
        }


class _RogueDecision:
    """A decision type the orchestrator has no handler for.

    The parser's closed DecisionKind set can never produce it and the
    orchestrator's isinstance chain cannot match it, so decide_turn falls
    through to the fatal "unhandled decision type" leg.
    """


class _RogueParser:
    """Parser seam stub: every LLM response decodes to the rogue decision."""

    def __init__(self) -> None:
        self.calls = 0

    def parse(self, raw):
        self.calls += 1
        return dm.DecisionParse(decision=_RogueDecision(), error=None)


def _fatal_env(db):
    """A real handler/repo stack whose orchestrator always decides fatally."""
    env = _build_env(
        db, [], FakeFactory(),
        llm=RepeatingLLMProvider(_spawn_decision("a1", "rogue objective")),
    )
    env.orchestrator._parser = _RogueParser()
    return env


class TestFatalOutcomeFinalizesFailedRun:

    def test_fatal_decision_fails_run_and_job_and_applies_retention(self, db):
        env = _fatal_env(db)
        run = _mk_run("fatal falls to failure")
        claim = _enqueue_and_claim(env, run)

        retention_calls: list = []
        original_retention = env.handler.store.apply_run_retention

        async def spy_retention(run_id, tenant):
            retention_calls.append((dm.RunId(run_id), tenant))
            return await original_retention(run_id, tenant)

        env.handler.store.apply_run_retention = spy_retention

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))

        assert outcome == "fail"
        stored = env.run_repo.get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.FAILED
        assert stored.termination_reason is dm.TerminationReason.ERROR
        assert stored.error == FATAL_TEXT
        assert stored.ended_at is not None

        dispatch = _dispatch_row(db, run.run_id)
        assert dispatch["status"] == "FAILED"
        assert dispatch["last_error"] == FATAL_TEXT

        # Retention/finalize ran exactly once, for this run.
        assert retention_calls == [(run.run_id, TENANT)]
        # A fatal slice leaves no work behind: no agents, no results.
        assert env.agent_repo.list_by_run(run.run_id, TENANT) == []
        assert env.result_repo.list_by_run(run.run_id, TENANT) == []

    def test_fenced_fatal_unwinds_as_lost_slice(self, db):
        env = _fatal_env(db)
        run = _mk_run()
        claim = _enqueue_and_claim(env, run)
        stale_epoch = claim["claim_epoch"]
        env.dispatch_repo.renew_lease(run.run_id, TENANT, WORKER, -1, stale_epoch)
        (reclaimed,) = env.dispatch_repo.reclaim_batch("worker-2", 10, 60)
        assert reclaimed["claim_epoch"] > stale_epoch

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, stale_epoch, worker_id=WORKER,
        ))

        # D28: the fenced fatal write raises _SliceLost inside
        # _finalize_failure — the reclaimer owns the run's outcome.
        assert outcome == "retry"
        stored = env.run_repo.get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.RUNNING
        assert stored.error is None


class TestStaleEpochForceFailIsFenced:

    def test_force_fail_fenced_by_claim_epoch_returns_false_touches_nothing(self, db):
        env = _build_env(db, [], FakeFactory())
        run = _mk_run()
        claim = _enqueue_and_claim(env, run)
        stale_epoch = claim["claim_epoch"]
        env.dispatch_repo.renew_lease(run.run_id, TENANT, WORKER, -1, stale_epoch)
        env.dispatch_repo.reclaim_batch("worker-2", 10, 60)

        ok = env.handler.force_fail(run.run_id, TENANT, stale_epoch, "boom", WORKER)

        # Fenced: the run write never landed...
        assert ok is False
        stored = env.run_repo.get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.RUNNING
        assert stored.error is None
        # ...and the dispatch was neither completed nor failed out from under
        # the worker that now owns the claim.
        dispatch = _dispatch_row(db, run.run_id)
        assert dispatch["status"] == "LEASED"
        assert dispatch["last_error"] is None
        assert dispatch["claimed_by"] == "worker-2"
