"""Durable-execution tests (task 10.7): queue semantics, claim fencing (D28),
reclaim circuit breaker (D40), crash-resume idempotency (D24), and
persona replay on rehydrate (D30) — real _TestDB, hand-fakes only.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, AgentSwarmOrchestrationConfig,
)
from agentclaw.community.core.agentswarm.runtime.worker import AgentSwarmWorker
from agentclaw.community.core.agentswarm.orchestration.termination import (
    TerminationEvaluator,
)
from tests.community.core.agentswarm.runtime.handler.test_swarm_slice import (
    TENANT, WORKER, _TestDB, FakeFactory, RepeatingLLMProvider, _build_env,
    _enqueue_and_claim, _finish_decision, _mk_run, _spawn_decision,
    _wait_decision, _spin_ref,
)

import dataclasses

from sqlalchemy.pool import StaticPool


@pytest.fixture()
def db():
    # The worker drives repositories via asyncio.to_thread, so the in-memory
    # SQLite engine must share one connection across threads (StaticPool),
    # unlike test_storage's default per-thread pool.
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
    yield _TestDB(engine)
    engine.dispose()


def _dispatch_state(db, run_id):
    with db.orm_session() as session:
        row = session.execute(
            select(om.AcAgentSwarmRunDispatchModel).where(
                om.AcAgentSwarmRunDispatchModel.run_id == run_id,
            )
        ).scalar_one()
        return {
            "status": row.status,
            "claim_epoch": row.claim_epoch,
            "claims": row.claims,
            "reclaims": row.reclaims,
            "progress_marker": row.progress_marker,
            "last_error": row.last_error,
        }


def _routed_result(env, run: dm.Run, index: int) -> None:
    # One routed agent result via the existing agent+result helper: each
    # call grows the run's routed-result count by one — the progress
    # signal the 4.5 breaker compares.
    _agent_with(env, run, f"a{index}", f"goal-a{index}",
                dm.AgentStatus.COMPLETED)


def _tight_config(**orch_overrides) -> AgentSwarmConfig:
    orch = AgentSwarmOrchestrationConfig(
        slice_max_seconds=0.2,
        slice_interval=0.0,
        orchestrator_turn_interval=0.01,
        reclaim_max=2,
        batch_size=4,
    )
    orch = dataclasses.replace(orch, **orch_overrides) if orch_overrides else orch
    return AgentSwarmConfig(orchestration=orch, message_retention_tail=50)


class TestClaimIdempotency:
    def test_claim_batch_never_double_claims(self, db):
        factory = FakeFactory()
        env = _build_env(db, [_finish_decision("x")], factory)
        run = _mk_run()
        env.run_repo.create(run)
        env.dispatch_repo.enqueue(run_id=run.run_id, tenant=TENANT, deadline_seconds=3600)

        first = env.dispatch_repo.claim_batch(WORKER, [TENANT], 1, 60)
        second = env.dispatch_repo.claim_batch("worker-2", [TENANT], 1, 60)

        assert len(first) == 1
        assert second == []
        assert env.dispatch_repo.claim_batch(WORKER, [TENANT], 1, 60) == []
        assert _dispatch_state(db, run.run_id)["claims"] == 1

    def test_reschedule_then_reclaim_bumps_epoch(self, db):
        factory = FakeFactory()
        env = _build_env(db, [_finish_decision("x")], factory)
        run = _mk_run()
        claim = _enqueue_and_claim(env, run)
        epoch1 = claim["claim_epoch"]

        assert env.dispatch_repo.reschedule(run.run_id, TENANT, WORKER, 0.0, None, epoch1)
        import time as _t
        _t.sleep(0.05)
        next_claim = env.dispatch_repo.claim_batch("worker-2", [TENANT], 1, 60)
        assert len(next_claim) == 1
        assert next_claim[0]["claim_epoch"] == epoch1 + 1


class TestClaimEpochFencing:
    def test_old_epoch_update_fenced_after_reclaim(self, db):
        factory = FakeFactory()
        env = _build_env(db, [_finish_decision("x")], factory)
        run = _mk_run()
        claim = _enqueue_and_claim(env, run)
        old_epoch = claim["claim_epoch"]

        # Simulate the leasing pod dying: lease expires, another worker reclaims.
        assert env.dispatch_repo.renew_lease(
            run.run_id, TENANT, WORKER, -1, old_epoch,
        )
        reclaimed = env.dispatch_repo.reclaim_batch("worker-2", 10, 60)
        assert len(reclaimed) == 1
        new_epoch = reclaimed[0]["claim_epoch"]
        assert new_epoch == old_epoch + 1

        stale_write = env.run_repo.update_status(
            run.run_id, TENANT, dm.RunStatus.COMPLETED,
            termination_reason=dm.TerminationReason.FINISH,
            claim_epoch=old_epoch,
        )
        assert stale_write is False
        assert env.run_repo.get(run.run_id, TENANT).status is dm.RunStatus.RUNNING

        legit_write = env.run_repo.update_status(
            run.run_id, TENANT, dm.RunStatus.COMPLETED,
            termination_reason=dm.TerminationReason.FINISH,
            final_answer="fenced ok",
            claim_epoch=new_epoch,
        )
        assert legit_write is True
        assert env.run_repo.get(run.run_id, TENANT).status is dm.RunStatus.COMPLETED


class TestReclaimCircuitBreaker:
    def test_no_progress_reclaims_trip_to_terminal_stuck(self, db):
        # A wait decision on a permanently-unsatisfied ref keeps the slice
        # non-terminal for its whole budget: the run can only progress via
        # reclaim, which the D40 breaker eventually terminates. The
        # turn-based no-progress evaluator is neutralized so it cannot
        # terminate the run before the breaker under test does.
        factory = FakeFactory()
        env = _build_env(
            db, [], factory, config=_tight_config(),
            llm=RepeatingLLMProvider(_wait_decision("ghost")),
            termination=TerminationEvaluator(no_progress_threshold=999),
        )
        run = _mk_run()
        _spin_ref(env, run)
        first = _enqueue_and_claim(env, run)
        env.dispatch_repo.renew_lease(
            run.run_id, TENANT, WORKER, -1, first["claim_epoch"],
        )
        worker = AgentSwarmWorker(
            config=env.cfg, handler=env.handler, dispatch_repo=env.dispatch_repo,
        )

        for _ in range(6):
            dispatch = _dispatch_state(db, run.run_id)
            if env.run_repo.get(run.run_id, TENANT).status is dm.RunStatus.STUCK:
                break
            if dispatch["status"] == "READY":
                victim = env.dispatch_repo.claim_batch("victim", [TENANT], 1, 60)
                assert victim, "dispatch should be claimable between cycles"
                env.dispatch_repo.renew_lease(
                    run.run_id, TENANT, "victim", -1, victim[0]["claim_epoch"],
                )
            asyncio.run(worker.run_once())

        stored = env.run_repo.get(run.run_id, TENANT)
        dispatch = _dispatch_state(db, run.run_id)
        assert stored.status is dm.RunStatus.STUCK
        assert stored.termination_reason == dm.TerminationReason.STUCK
        assert "reclaim circuit breaker" in stored.error
        assert dispatch["reclaims"] == 3
        assert dispatch["status"] == "DONE"
        assert stored.ended_at is not None
        # 4.5 mixed-version pin: the flat result count stayed the claim
        # baseline throughout — the breaker never saw growth and the
        # marker never reset anything.
        assert dispatch["progress_marker"] == 0


class TestProgressAwareReclaimBreaker:
    """4.5 on the repository surface: the claim/reclaim UPDATE persists
    the run's routed-result count into progress_marker; growth across a
    crash window resets reclaims, a flat count increments exactly as
    before, and the -1 sentinel (a row from a pre-marker version) is a
    never-progress baseline."""

    def test_claim_persists_routed_result_count_as_baseline(self, db):
        factory = FakeFactory()
        env = _build_env(db, [], factory, config=_tight_config())
        run = _mk_run()
        env.run_repo.create(run)
        _routed_result(env, run, 1)
        _routed_result(env, run, 2)
        env.dispatch_repo.enqueue(run_id=run.run_id, tenant=TENANT,
                             deadline_seconds=3600)

        claim = env.dispatch_repo.claim_batch(WORKER, [TENANT], 1, 60)[0]

        assert claim["progress_marker"] == 2
        assert _dispatch_state(db, run.run_id)["progress_marker"] == 2

    def test_reclaim_with_result_growth_resets_reclaims(self, db):
        factory = FakeFactory()
        env = _build_env(db, [], factory, config=_tight_config())
        run = _mk_run()
        claim = _enqueue_and_claim(env, run)
        assert _dispatch_state(db, run.run_id)["progress_marker"] == 0

        _routed_result(env, run, 1)
        env.dispatch_repo.renew_lease(
            run.run_id, TENANT, WORKER, -1, claim["claim_epoch"],
        )
        first = env.dispatch_repo.reclaim_batch("worker-2", 1, 60)[0]
        assert first["reclaims"] == 0
        assert first["progress_marker"] == 1

        # No further growth: the next reclaim increments again.
        env.dispatch_repo.renew_lease(
            run.run_id, TENANT, "worker-2", -1, first["claim_epoch"],
        )
        second = env.dispatch_repo.reclaim_batch("worker-3", 1, 60)[0]
        assert second["reclaims"] == 1

    def test_reclaim_without_growth_increments_as_before(self, db):
        factory = FakeFactory()
        env = _build_env(db, [], factory, config=_tight_config())
        run = _mk_run()
        claim = _enqueue_and_claim(env, run)

        env.dispatch_repo.renew_lease(
            run.run_id, TENANT, WORKER, -1, claim["claim_epoch"],
        )
        first = env.dispatch_repo.reclaim_batch("worker-2", 1, 60)[0]
        assert first["reclaims"] == 1

        env.dispatch_repo.renew_lease(
            run.run_id, TENANT, "worker-2", -1, first["claim_epoch"],
        )
        second = env.dispatch_repo.reclaim_batch("worker-3", 1, 60)[0]
        assert second["reclaims"] == 2

    def test_minus_one_marker_row_is_baseline_never_progress(self, db):
        factory = FakeFactory()
        env = _build_env(db, [], factory, config=_tight_config())
        run = _mk_run()
        env.run_repo.create(run)
        _routed_result(env, run, 1)
        env.dispatch_repo.enqueue(run_id=run.run_id, tenant=TENANT,
                             deadline_seconds=3600)
        # Simulate a claim by a pre-marker version: RUNNING with an
        # expired lease, progress_marker still at its -1 default.
        with db.orm_session() as session:
            session.execute(
                om.AcAgentSwarmRunDispatchModel.__table__.update().where(
                    om.AcAgentSwarmRunDispatchModel.run_id == run.run_id,
                ).values(
                    status=dm.DispatchStatus.LEASED.value,
                    claim_epoch=1,
                    lease_expires_at=datetime(2020, 1, 1),
                )
            )

        reclaimed = env.dispatch_repo.reclaim_batch("worker-2", 1, 60)[0]

        # First observation = baseline: growth vs -1 is NOT progress.
        assert reclaimed["reclaims"] == 1
        assert reclaimed["progress_marker"] == 1
        assert reclaimed["claim_epoch"] == 2


class TestRollingRestartWithProgressSurvivesBreaker:
    """4.6 (i): a crash-looping run whose routed-result count grows
    between reclaims survives past reclaim_max — the exact rolling
    restart of a long bot run D40 used to kill."""

    def test_result_growth_between_reclaims_survives_past_reclaim_max(self, db):
        factory = FakeFactory()
        env = _build_env(
            db, [], factory, config=_tight_config(),
            llm=RepeatingLLMProvider(_wait_decision("ghost")),
            termination=TerminationEvaluator(no_progress_threshold=999),
        )
        run = _mk_run()
        _spin_ref(env, run)
        claim = _enqueue_and_claim(env, run)
        env.dispatch_repo.renew_lease(
            run.run_id, TENANT, WORKER, -1, claim["claim_epoch"],
        )
        worker = AgentSwarmWorker(
            config=env.cfg, handler=env.handler, dispatch_repo=env.dispatch_repo,
        )

        # reclaim_max is 2 in _tight_config: three growth cycles plus a
        # flat one all stay under the breaker (0 or 1 <= 2).
        for cycle in range(3):
            dispatch = _dispatch_state(db, run.run_id)
            if dispatch["status"] == "READY":
                victim = env.dispatch_repo.claim_batch("victim", [TENANT], 1, 60)
                assert victim
                env.dispatch_repo.renew_lease(
                    run.run_id, TENANT, "victim", -1, victim[0]["claim_epoch"],
                )
            # Growth lands BETWEEN claim and reclaim (mid-flight crash
            # after routing) — the reclaimer-visible window.
            _routed_result(env, run, cycle)
            asyncio.run(worker.run_once())
            assert env.run_repo.get(run.run_id, TENANT).status \
                is dm.RunStatus.RUNNING
        assert _dispatch_state(db, run.run_id)["reclaims"] == 0

        # One no-growth reclaims cycle does not trip it either.
        dispatch = _dispatch_state(db, run.run_id)
        if dispatch["status"] == "READY":
            victim = env.dispatch_repo.claim_batch("victim", [TENANT], 1, 60)
            assert victim
            env.dispatch_repo.renew_lease(
                run.run_id, TENANT, "victim", -1, victim[0]["claim_epoch"],
            )
        asyncio.run(worker.run_once())
        stored = env.run_repo.get(run.run_id, TENANT)
        stored_dispatch = _dispatch_state(db, run.run_id)
        assert stored.status is dm.RunStatus.RUNNING
        assert stored_dispatch["reclaims"] == 1
        assert stored_dispatch["progress_marker"] == 3


class TestClaimPastDeadline:
    """4.4/4.6 (vi): `deadline_at` never joins the claim predicate — a
    past-deadline dispatch still claims, and the rehydrated slice's
    TerminationEvaluator fires the fenced TIMED_OUT through the normal
    path."""

    def test_past_deadline_job_claims_and_slice_fences_timed_out(self, db):
        factory = FakeFactory()
        env = _build_env(
            db, [], factory, config=_tight_config(),
            llm=RepeatingLLMProvider(_wait_decision("ghost")),
            termination=TerminationEvaluator(
                wall_clock_seconds=0.05, no_progress_threshold=999),
        )
        run = _mk_run()
        run.started_at = dm.EpochMillis(int(time.time() * 1000) - 1_000)
        env.run_repo.create(run)
        env.dispatch_repo.enqueue(run_id=run.run_id, tenant=TENANT,
                             deadline_seconds=3600)
        # Push the row's deadline_at into the past: a claim must ignore it.
        with db.orm_session() as session:
            session.execute(
                om.AcAgentSwarmRunDispatchModel.__table__.update().where(
                    om.AcAgentSwarmRunDispatchModel.run_id == run.run_id,
                ).values(deadline_at=datetime(2020, 1, 1))
            )

        claimed = env.dispatch_repo.claim_batch(WORKER, [TENANT], 1, 60)

        # 4.4: past-deadline rows REMAIN claimable.
        assert len(claimed) == 1
        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claimed[0]["claim_epoch"], worker_id=WORKER,
        ))

        assert outcome == "complete"
        stored = env.run_repo.get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.TIMED_OUT
        assert stored.termination_reason is dm.TerminationReason.WALL_CLOCK
        assert _dispatch_state(db, run.run_id)["status"] == "DONE"


def _agent_with(env, run: dm.Run, name: str, objective: str, status: dm.AgentStatus,
                attempts: int = 0, *, result: bool = True) -> dm.Agent:
    agent = dm.Agent(
        agent_id=dm.AgentId(uuid.uuid4().hex),
        run_id=run.run_id,
        tenant=TENANT,
        parent_agent_id=None,
        depth=0,
        name=name,
        role=objective,
        kind=dm.AgentKind.LLM,
        task_spec=dm.TaskEnvelope(
            name=name, objective=objective, output_schema={},
            boundaries="", parent_goal=run.goal,
        ),
        spawn_round=0,
        status=status,
        attempts=attempts,
        idempotency_key=dm.IdempotencyKey(uuid.uuid4().hex),
        output={},
    )
    env.agent_repo.create(agent)
    if result:
        env.result_repo.create(dm.AgentResult(
            id=dm.ResultId(uuid.uuid4().hex),
            run_id=run.run_id,
            tenant=TENANT,
            source_agent_id=agent.agent_id,
            payload={"result": f"done-{name}"},
            artifact_key=None,
            routed_at=dm.EpochMillis(0),
        ))
    return agent


class TestResumeNeverRerunCompleted:
    def test_completed_agent_with_result_is_never_re_spawned(self, db):
        script = [
            _spawn_decision("a1", "goal-a1"),
            _finish_decision("done-2"),
        ]
        factory = FakeFactory()
        env = _build_env(db, script, factory)
        run = _mk_run()
        done = _agent_with(env, run, "a1", "goal-a1", dm.AgentStatus.COMPLETED,
                           attempts=1, result=True)
        claim = _enqueue_and_claim(env, run)

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))

        assert outcome == "complete"
        assert factory.creates.get("a1", 0) == 0
        assert [e for e in factory.run_effects if e[0] == "a1"] == []
        stored_agent = env.agent_repo.get(run.run_id, done.agent_id, TENANT)
        assert stored_agent.status is dm.AgentStatus.COMPLETED
        assert stored_agent.attempts == 1
        stored_run = env.run_repo.get(run.run_id, TENANT)
        assert stored_run.status is dm.RunStatus.COMPLETED
        assert stored_run.final_answer == "done-2"
        # Exactly one result for this agent — no duplicate side effects.
        assert len(env.result_repo.list_by_run(run.run_id, TENANT)) == 1

    def test_running_agent_with_existing_result_flips_completed_idempotently(self, db):
        script = [_finish_decision("done-3")]
        factory = FakeFactory()
        env = _build_env(db, script, factory)
        run = _mk_run()
        done = _agent_with(env, run, "a1", "goal-a1", dm.AgentStatus.RUNNING,
                           attempts=2, result=True)
        claim = _enqueue_and_claim(env, run)

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))

        assert outcome == "complete"
        stored = env.agent_repo.get(run.run_id, done.agent_id, TENANT)
        assert stored.status is dm.AgentStatus.COMPLETED
        assert factory.creates == {}
        assert _dispatch_state(db, run.run_id)["status"] == "DONE"

    def test_interrupted_agent_without_result_resumes_same_identity(self, db):
        # D16: an interrupted (no result) agent is re-instantiated under its
        # existing AgentId so the resume produces exactly one persistent agent.
        script = [
            _spawn_decision("a1", "goal-a1"),
            _wait_decision("a1"),
            _finish_decision("recovered"),
        ]
        factory = FakeFactory(scripts={"a1": [{"resumed": True}]})
        env = _build_env(db, script, factory)
        run = _mk_run()
        _agent_with(env, run, "a1", "goal-a1", dm.AgentStatus.QUEUED,
                    attempts=1, result=False)
        claim = _enqueue_and_claim(env, run)

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))

        assert outcome == "complete"
        agents = env.agent_repo.list_by_run(run.run_id, TENANT)
        assert len(agents) == 1
        assert agents[0].status is dm.AgentStatus.COMPLETED
        assert agents[0].attempts == 1
        assert factory.creates.get("a1") == 1


class TestPersonaReplayAcrossSlices:
    def test_handoff_decision_rehydrates_active_persona(self, db):
        from agentclaw.community.core.agentswarm.domain.models import (
            MessageRole,
        )
        env_a = _build_env(
            db, [json.dumps({"kind": "handoff", "target": "a2"}),
                 _wait_decision("ghost")],
            FakeFactory(), config=_tight_config(),
        )
        run = _mk_run()
        _spin_ref(env_a, run)
        claim = _enqueue_and_claim(env_a, run)

        outcome_a = asyncio.run(env_a.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))
        # Handoff does not terminate; the tiny budget expires and reschedules.
        assert outcome_a == "reschedule"
        decision_rows = env_a.message_repo.list_by_agent(
            run.run_id, dm.AgentId(run.run_id), TENANT,
        )
        assert any(
            m.role is MessageRole.DECISION and m.content.get("kind") == "handoff"
            for m in decision_rows
        )

        # Slice B on a fresh claim rehydrates the persona from the persisted
        # decision message; its USER prompt must reflect it (D30 replay).
        env_a.llm.set_script([_finish_decision("persona done")])
        claim_b = env_a.dispatch_repo.claim_batch(WORKER, [TENANT], 1, 60)
        outcome_b = asyncio.run(env_a.handler.handle_slice(
            run.run_id, TENANT, claim_b[0]["claim_epoch"], worker_id=WORKER,
        ))
        assert outcome_b in {"complete", "fail"}
        user_prompts = [
            m.content for m in env_a.message_repo.list_by_agent(
                run.run_id, dm.AgentId(run.run_id), TENANT,
            )
            if m.role is MessageRole.USER and isinstance(m.content, str)
        ]
        assert any("active_persona=a2" in p for p in user_prompts)


class TestTerminationOnDedupKnownObjective:
    def test_resume_after_terminal_run_completes_job_without_new_slices(self, db):
        env = _build_env(db, [_finish_decision("x")], FakeFactory())
        run = _mk_run()
        env.run_repo.create(run)
        env.dispatch_repo.enqueue(run_id=run.run_id, tenant=TENANT, deadline_seconds=3600)
        claim = env.dispatch_repo.claim_batch(WORKER, [TENANT], 1, 60)[0]
        env.run_repo.update_status(
            run.run_id, TENANT, dm.RunStatus.CANCELLED,
            termination_reason=dm.TerminationReason.CANCELLED,
            claim_epoch=claim["claim_epoch"],
        )
        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))
        # Already-terminal runs no-op: the dispatch is completed, nothing re-runs.
        assert outcome == "complete"
        assert _dispatch_state(db, run.run_id)["status"] == "DONE"


class _FencedFailingHandler:
    """Stub SwarmRunHandler: handle_slice always raises and force_fail is
    fenced (returns False, as after a D28 claim-loss), so the worker's
    dispatch_repo.fail fallback leg is the behavior on trial."""

    def __init__(self, error: Exception) -> None:
        self.slice_error = error
        self.calls: list = []

    def set_slice_lost(self, lost, run_id=None):
        self.calls.append(("set-slice-lost", lost, run_id))

    async def handle_slice(self, run_id, tenant, claim_epoch,
                           cancel_requested=False, *, worker_id=None):
        self.calls.append(("handle-slice", run_id, claim_epoch))
        raise self.slice_error

    def force_stuck(self, run_id, tenant, epoch, error, worker_id=None):
        self.calls.append(("force-stuck", run_id, epoch, error, worker_id))
        return True

    def force_fail(self, run_id, tenant, epoch, error, worker_id=None):
        self.calls.append(("force-fail", run_id, epoch, error, worker_id))
        return False  # fenced: a newer claim rejected this worker's run write


class _RecordingJobRepo:
    """Minimal RunDispatchRepository spy — only the fallback `fail` can land."""

    def __init__(self) -> None:
        self.fails: list = []

    def reclaim_batch(self, worker_id, limit, lease_seconds):
        return []

    def claim_batch(self, worker_id, tenant_filter, limit, lease_seconds):
        return []

    def renew_lease(self, run_id, tenant, worker_id, lease_seconds, claim_epoch):
        return True

    def fail(self, run_id, tenant, error, worker_id, claim_epoch):
        self.fails.append((dm.RunId(run_id), tenant, error, worker_id, claim_epoch))
        return True


class TestWorkerFencedFailureFallsBackToJobFail:

    def test_fenced_force_fail_fails_durable_job_with_expected_args(self):
        error = RuntimeError("storage exploded mid-slice")
        handler = _FencedFailingHandler(error)
        repo = _RecordingJobRepo()
        worker = AgentSwarmWorker(
            config=_tight_config(), handler=handler, dispatch_repo=repo,
        )
        worker._worker_id = "w-fenced"
        dispatch = {
            "run_id": dm.RunId("fenced-run"), "tenant": TENANT,
            "claim_epoch": 7, "reclaims": 0, "cancel_requested": False,
        }

        asyncio.run(worker._run_dispatch(dispatch, reclaimed=False))

        # force_fail was attempted first, carrying the slice error and this
        # worker's identity...
        assert ("force-fail", dm.RunId("fenced-run"), 7,
                "storage exploded mid-slice", "w-fenced") in handler.calls
        # ...and came back fenced, so the fallback must mark the durable
        # dispatch failed with exactly (run_id, tenant, error, worker_id, epoch).
        assert repo.fails == [
            (dm.RunId("fenced-run"), TENANT, "storage exploded mid-slice",
             "w-fenced", 7),
        ]
        # The in-flight ledger is cleared either way.
        assert "fenced-run" not in worker._inflight