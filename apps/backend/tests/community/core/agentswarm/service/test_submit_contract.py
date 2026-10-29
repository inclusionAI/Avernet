"""Async-submit contract conformance gates (tasks 3.1-3.3):

Submit must never execute the run: it hands a durable run row plus a
READY dispatch to the queue and returns only the run_id, in well under two
seconds, even for a goal that would take dozens of orchestrator turns.
Admission failures (tenant cap, missing LLM config) raise synchronously
in the submit call and persist nothing. Once accepted, the run converges
through the durable dispatch queue alone — the submitting client can vanish
immediately and nothing changes, and the idempotency-join semantics
survive the disconnect (same key joins while RUNNING; a terminal write
nulls `active_idempotency_key`, so the same key later opens a FRESH run).

Real repositories over in-memory SQLite (the module's house style),
StubLLMProvider orchestrator scripts, and the hand-fake SubagentFactory
from test_swarm_slice; the worker runs paused (`enabled=False`, the
lifecycle startup switch) and slices are driven manually through
`AgentSwarmWorker.run_once()` exactly like test_e2e.py.

The HTTP-plane 429/400 mappings for these typed service errors are
locked separately in tests/community/adapters/http/agentswarm/.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, AgentSwarmOrchestrationConfig,
    AgentSwarmTenantLimits, LlmConfig,
)
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.orchestration.decision_parser import (
    DecisionParser,
)
from agentclaw.community.core.agentswarm.orchestration.guards import GuardService
from agentclaw.community.core.agentswarm.orchestration.orchestrator import (
    Orchestrator,
)
from agentclaw.community.core.agentswarm.orchestration.termination import (
    TerminationEvaluator,
)
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.repository.agent import AgentRepository
from agentclaw.community.core.agentswarm.repository.guard import (
    GuardEventRepository,
)
from agentclaw.community.core.agentswarm.repository.run_dispatch import RunDispatchRepository
from agentclaw.community.core.agentswarm.repository.message import (
    MessageRepository,
)
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.core.agentswarm.repository.agent_result import (
    AgentResultRepository,
)
from agentclaw.community.core.agentswarm.runtime.handler import SwarmRunHandler
from agentclaw.community.core.agentswarm.runtime.pool import SubagentRuntime
from agentclaw.community.core.agentswarm.runtime.worker import AgentSwarmWorker
from agentclaw.community.core.agentswarm.service import AgentSwarmService
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentSwarmConfigError, SubmitRunRequest, TenantRunLimitExceeded,
)
from agentclaw.community.plugins.local.agentswarm.llm_stub import (
    StubLLMProvider,
)
from tests.community.core.agentswarm.runtime.handler.test_swarm_slice import (
    FakeFactory, _TestDB, _finish_decision, _spawn_decision, _wait_decision,
)

TENANT = "tenant-a"
TID = dm.TenantId(TENANT)


@pytest.fixture()
def db():
    # The worker drives repositories via asyncio.to_thread, so the engine
    # must share one connection across threads (same convention as
    # test_e2e.py / test_durable_exec.py).
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
    yield _TestDB(engine)
    engine.dispose()


def _paused_config(*, tenant_limit: int = 8) -> AgentSwarmConfig:
    """Worker paused: `enabled=False` is the knob the app lifecycle
    consults (AgentSwarmWorker.startup no-ops), so nothing executes
    until this test drives slices manually."""
    return AgentSwarmConfig(
        llm=LlmConfig(base_url="stub", api_key="stub", default_model="stub"),
        orchestration=AgentSwarmOrchestrationConfig(
            # 15 spawn + 15 wait turns below land well inside the default
            # 50-turn budget; slice budget sized to converge in one slice.
            slice_max_seconds=8.0,
            slice_interval=0.0,
            orchestrator_turn_interval=0.01,
            agent_max_attempts=1,
            batch_size=4,
        ),
        tenant_limits=AgentSwarmTenantLimits(max_concurrent_runs=tenant_limit),
        enabled=False,
        message_retention_tail=50,
    )


def _build_env(db, script: list[str], *,
               config: AgentSwarmConfig | None = None,
               ) -> SimpleNamespace:
    cfg = config or _paused_config()
    repos = {
        "run": RunRepository(db),
        "agent": AgentRepository(db),
        "message": MessageRepository(db),
        "result": AgentResultRepository(db),
        "round": RoundRepository(db),
        "guard": GuardEventRepository(db),
        "dispatch": RunDispatchRepository(db),
    }
    llm = StubLLMProvider(script=script)
    orchestrator = Orchestrator(
        llm=llm,
        parser=DecisionParser(),
        guards=GuardService(
            max_width=cfg.orchestration.max_width,
            max_depth=cfg.orchestration.max_depth,
        ),
        config=cfg,
    )
    runtime = SubagentRuntime(
        factory=FakeFactory(),
        max_width=cfg.orchestration.max_width,
        max_attempts=cfg.orchestration.agent_max_attempts,
        per_task_timeout=cfg.orchestration.per_attempt_timeout,
        artifact_inline_threshold=cfg.artifact_inline_threshold,
    )
    handler = SwarmRunHandler(
        config=cfg, orchestrator=orchestrator, runtime=runtime,
        run_repo=repos["run"], agent_repo=repos["agent"],
        message_repo=repos["message"], result_repo=repos["result"],
        round_repo=repos["round"], guard_repo=repos["guard"],
        dispatch_repo=repos["dispatch"],
        termination=TerminationEvaluator(max_turns=cfg.orchestration.max_turns),
        guards=GuardService(),
    )
    worker = AgentSwarmWorker(
        config=cfg, handler=handler, dispatch_repo=repos["dispatch"],
    )
    service = AgentSwarmService(
        run_repo=repos["run"], dispatch_repo=repos["dispatch"],
        agent_repo=repos["agent"], message_repo=repos["message"],
        round_repo=repos["round"], guard_repo=repos["guard"],
        result_repo=repos["result"], config=cfg,
    )
    return SimpleNamespace(
        cfg=cfg, llm=llm, handler=handler, worker=worker, service=service,
        run_repo=repos["run"], agent_repo=repos["agent"],
        message_repo=repos["message"], result_repo=repos["result"],
        round_repo=repos["round"], guard_repo=repos["guard"],
        dispatch_repo=repos["dispatch"],
    )


def _service_with(db, config: AgentSwarmConfig) -> AgentSwarmService:
    return AgentSwarmService(
        run_repo=RunRepository(db), dispatch_repo=RunDispatchRepository(db),
        agent_repo=AgentRepository(db), message_repo=MessageRepository(db),
        round_repo=RoundRepository(db), guard_repo=GuardEventRepository(db),
        result_repo=AgentResultRepository(db), config=config,
    )


def _req(*, idempotency_key: str | None = None,
         goal: str = "submit-contract goal") -> SubmitRunRequest:
    return SubmitRunRequest(
        tenant=TENANT,
        goal=goal,
        agents=(dm.AgentSpec(name="a1", role="research"),),
        idempotency_key=idempotency_key,
    )


def _tenant_rows(db, model) -> int:
    with db.orm_session() as session:
        return session.execute(
            select(func.count()).select_from(model).where(
                model.tenant == TID,
                model.is_deleted.is_(False),
            )
        ).scalar_one()


def _dispatch_status(db, run_id: str) -> str:
    with db.orm_session() as session:
        row = session.execute(
            select(om.AcAgentSwarmRunDispatchModel).where(
                om.AcAgentSwarmRunDispatchModel.run_id == dm.RunId(run_id),
                om.AcAgentSwarmRunDispatchModel.tenant == TID,
            )
        ).scalar_one()
        return row.status


async def _drive_to_terminal(worker: AgentSwarmWorker, run_repo: RunRepository,
                             run_id: str, max_ticks: int = 40) -> dm.Run | None:
    for _ in range(max_ticks):
        await worker.run_once()
        run = run_repo.get(dm.RunId(run_id), TID)
        if run is not None and run.status is not dm.RunStatus.RUNNING:
            return run
        await asyncio.sleep(0.005)
    return run_repo.get(dm.RunId(run_id), TID)


class TestNonBlockingSubmit:
    """3.1: submit of a 30+-turn run returns fast carrying only run_id,
    with zero execution rows at return; the paused run converges once
    the worker's durable queue is driven."""

    @pytest.mark.asyncio
    async def test_submit_of_30_turn_goal_returns_fast_then_converges(
            self, db):
        # A goal that would take 30 orchestrator turn-decisions to run:
        # spawn+wait over 15 agents (every wait blocks the turn on a
        # routed result, so the turn battery is genuinely long).
        script: list[str] = []
        for i in range(1, 16):
            script.append(_spawn_decision(f"a{i}", f"objective-a{i}"))
            script.append(_wait_decision(f"a{i}"))
        script.append(_finish_decision("converged after unpause"))
        env = _build_env(db, script)

        # The pause knob, pinned: enabled=False makes the lifecycle
        # startup a no-op — no poll loop, no lease heartbeats.
        await env.worker.startup()
        assert env.worker._task is None
        assert env.worker._running is False

        started = time.monotonic()
        run_id = await env.service.submit_async(
            _req(idempotency_key="pause-42", goal="30-turn goal"),
        )
        elapsed = time.monotonic() - started

        # Non-blocking submit: a bare run_id, well inside the 2s bound
        # (measured, not just plausible — the same script executed inline
        # would burn 30 orchestrator turn cycles in the request).
        assert isinstance(run_id, str) and run_id
        assert elapsed < 2.0

        rid = dm.RunId(run_id)
        run = env.service.get_run(run_id, TENANT)
        assert run is not None
        assert run.status is dm.RunStatus.RUNNING
        # The durable pair the contract requires: a run row plus one
        # READY dispatch for the paused worker's queue — and that is ALL.
        assert env.dispatch_repo.count_pending_dispatches() == 1
        assert _tenant_rows(db, om.AcAgentSwarmRunModel) == 1
        assert _tenant_rows(db, om.AcAgentSwarmRunDispatchModel) == 1
        assert _tenant_rows(db, om.AcAgentSwarmAgentModel) == 0
        assert _tenant_rows(db, om.AcAgentSwarmAgentResultModel) == 0
        assert _tenant_rows(db, om.AcAgentSwarmMessageModel) == 0
        assert env.agent_repo.list_by_run(rid, TID) == []
        assert env.result_repo.list_by_run(rid, TID) == []
        assert env.round_repo.list_by_run(rid, TID) == []
        assert env.guard_repo.list_by_run(rid, TID) == []

        # Unpause: drive worker slices manually — the durable dispatch queue
        # is the only execution driver — until the run converges.
        run = await _drive_to_terminal(env.worker, env.run_repo, run_id)
        assert run is not None, "run row vanished"
        assert run.status is dm.RunStatus.COMPLETED
        assert run.termination_reason is dm.TerminationReason.FINISH
        assert run.final_answer == "converged after unpause"
        assert run.ended_at is not None
        assert _dispatch_status(db, run_id) == "DONE"

        # Rows materialized by the converged turns: 15 completed agents,
        # 15 routed results, decision + per-agent messages.
        agents = env.agent_repo.list_by_run(rid, TID)
        assert len(agents) == 15
        assert all(a.status is dm.AgentStatus.COMPLETED for a in agents)
        results = env.result_repo.list_by_run(rid, TID)
        assert len(results) == 15
        assert all(s.payload is not None for s in results)
        decisions = env.message_repo.list_by_agent(
            rid, dm.AgentId(run_id), TID,
        )
        assert any(m.role is dm.MessageRole.DECISION for m in decisions)
        assert len(
            env.message_repo.list_by_agent(rid, agents[0].agent_id, TID)
        ) >= 1


class TestAdmissionErrorsAreSynchronous:
    """3.2: the 429-class tenant cap and the 400-class LLM-config gate
    raise inside the submit call itself and leave zero durable rows —
    admission is strictly accept-or-reject."""

    @pytest.mark.asyncio
    async def test_tenant_limit_exceeded_raises_and_persists_nothing(
            self, db):
        env = _build_env(
            db, [_finish_decision("x")],
            config=_paused_config(tenant_limit=1),
        )
        admitted = await env.service.submit_async(
            _req(idempotency_key="hold-1", goal="admitted goal"),
        )
        assert admitted

        with pytest.raises(TenantRunLimitExceeded) as caught:
            await env.service.submit_async(
                _req(idempotency_key="rejected-key", goal="rejected goal"),
            )
        assert caught.value.tenant == TENANT
        assert caught.value.limit == 1

        # Only the admitted run's rows persist: the rejected submit added
        # no run, dispatch, agent, result, or message row anywhere.
        assert _tenant_rows(db, om.AcAgentSwarmRunModel) == 1
        assert _tenant_rows(db, om.AcAgentSwarmRunDispatchModel) == 1
        assert _tenant_rows(db, om.AcAgentSwarmAgentModel) == 0
        assert _tenant_rows(db, om.AcAgentSwarmAgentResultModel) == 0
        assert _tenant_rows(db, om.AcAgentSwarmMessageModel) == 0
        # The rejected request's idempotency key holds no slot either.
        assert env.run_repo.get_by_idempotency("rejected-key", TID) is None

    @pytest.mark.asyncio
    async def test_missing_or_blank_llm_config_raises_and_persists_nothing(
            self, db):
        for bad_llm in (None, LlmConfig(base_url="")):
            config = AgentSwarmConfig(llm=bad_llm)
            service = _service_with(db, config)
            with pytest.raises(AgentSwarmConfigError):
                await service.submit_async(
                    _req(idempotency_key=f"cfg-{id(bad_llm)}"),
                )
            # Fail-clear keeps it fail-EMPTY: over a shared store, the
            # both-rejected pass still leaves every table at zero rows.
            assert _tenant_rows(db, om.AcAgentSwarmRunModel) == 0
            assert _tenant_rows(db, om.AcAgentSwarmRunDispatchModel) == 0
            assert _tenant_rows(db, om.AcAgentSwarmAgentModel) == 0
            assert _tenant_rows(db, om.AcAgentSwarmAgentResultModel) == 0
            assert _tenant_rows(db, om.AcAgentSwarmMessageModel) == 0
            assert _tenant_rows(db, om.AcAgentSwarmRoundModel) == 0
            assert _tenant_rows(db, om.AcAgentSwarmGuardEventModel) == 0


class TestSubmitterDetach:
    """3.3: submitter-detach — a client that vanishes immediately after
    submit changes nothing: the run converges through the queue with
    rows materialized, the duplicate-key join stays intact during the
    flight, and a terminal write releases the slot so the same key later
    opens a FRESH run."""

    @pytest.mark.asyncio
    async def test_vanished_client_converges_and_key_join_survives(
            self, db):
        script = [
            _spawn_decision("a1", "objective-a1"),
            _wait_decision("a1"),
            _finish_decision("detach done"),
        ]
        env = _build_env(db, script)
        await env.worker.startup()
        assert env.worker._task is None

        # The client submits and vanishes: between this call and the
        # worker ticks below it never polls, cancels, or watches — the
        # service returns a run_id, never an execution handle to lose.
        run_id = await env.service.submit_async(
            _req(idempotency_key="detach-42", goal="detach goal"),
        )

        # The keyed duplicate (a second client with the same key) joins
        # the live run — same run_id, still exactly one durable dispatch.
        joined = await env.service.submit_async(
            _req(idempotency_key="detach-42", goal="detach goal"),
        )
        assert joined == run_id
        assert env.dispatch_repo.count_pending_dispatches() == 1

        run = await _drive_to_terminal(env.worker, env.run_repo, run_id)
        assert run is not None, "run row vanished"
        assert run.status is dm.RunStatus.COMPLETED
        assert run.final_answer == "detach done"
        assert _dispatch_status(db, run_id) == "DONE"
        rid = dm.RunId(run_id)
        agents = env.agent_repo.list_by_run(rid, TID)
        # Spawn-local composite name: base stem carries the envelope name.
        assert [a.name.split("_", 1)[0] for a in agents] == ["a1"]
        assert agents[0].status is dm.AgentStatus.COMPLETED
        assert len(env.result_repo.list_by_run(rid, TID)) == 1
        assert any(
            m.role is dm.MessageRole.DECISION for m in
            env.message_repo.list_by_agent(rid, dm.AgentId(run_id), TID)
        )
        assert _tenant_rows(db, om.AcAgentSwarmRunDispatchModel) == 1

        # Terminal write nulls the active idempotency key
        # (repository/run/impl.py TerminationEvaluator fence and slot
        # release): the completed run holds no join slot anymore.
        stored = env.run_repo.get(rid, TID)
        assert stored is not None
        assert stored.active_idempotency_key is None

        # Same key after terminal -> a FRESH run, never a resurrect of
        # the completed one, and the slot belongs to the new run.
        fresh = await env.service.submit_async(
            _req(idempotency_key="detach-42", goal="detach goal"),
        )
        assert fresh != run_id
        fresh_run = env.run_repo.get(dm.RunId(fresh), TID)
        assert fresh_run is not None
        assert fresh_run.status is dm.RunStatus.RUNNING
        joined_now = env.run_repo.get_by_idempotency("detach-42", TID)
        assert joined_now is not None
        assert joined_now.run_id == dm.RunId(fresh)
        # The detached, converged run stays terminal and untouched.
        old = env.run_repo.get(rid, TID)
        assert old.status is dm.RunStatus.COMPLETED
        assert old.active_idempotency_key is None
        assert old.final_answer == "detach done"