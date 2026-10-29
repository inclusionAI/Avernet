"""Single-process end-to-end integration test (OpenSpec 10.11):

submit (service + idempotency) -> durable dispatch -> worker claim (fenced
epoch) -> orchestrator decisions (scripted LLM) -> subagent execution
(fake factory) -> result/message/round persistence -> terminal publish +
retention, all against one real in-memory SQLite store.

No network, no FastAPI: this exercises the assembled core pipeline —
AgentSwarmService, AgentSwarmWorker, SwarmRunHandler, SliceStore,
Orchestrator, DecisionParser, GuardService, TerminationEvaluator — with
the StubLLMProvider script and a hand-fake SubagentFactory (D42-safe:
empty output schemas).
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, AgentSwarmOrchestrationConfig, LlmConfig,
)
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.orchestration.orchestrator import (
    Orchestrator,
)
from agentclaw.community.core.agentswarm.orchestration.guards import GuardService
from agentclaw.community.core.agentswarm.orchestration.termination import (
    TerminationEvaluator,
)
from agentclaw.community.core.agentswarm.orchestration.decision_parser import (
    DecisionParser,
)
from agentclaw.community.plugins.local.agentswarm.llm_stub import StubLLMProvider
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.repository.agent import AgentRepository
from agentclaw.community.core.agentswarm.repository.guard import GuardEventRepository
from agentclaw.community.core.agentswarm.repository.run_dispatch import RunDispatchRepository
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.agent_result import AgentResultRepository
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.core.agentswarm.runtime.pool import (
    SubagentRuntime,
)
from agentclaw.community.core.agentswarm.runtime.handler import SwarmRunHandler
from agentclaw.community.core.agentswarm.runtime.worker import AgentSwarmWorker
from agentclaw.community.core.agentswarm.service import AgentSwarmService
from agentclaw.community.core.agentswarm.service.protocols import SubmitRunRequest

TENANT = dm.TenantId("tenant-a")
WORKER = "worker-e2e"


class _TestDB:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

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
    # The worker drives repositories via asyncio.to_thread, so the engine
    # must share one connection across threads (same convention as
    # test_durable_exec).
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
    yield _TestDB(engine)
    engine.dispose()


class _FakeSubagent:
    name = "fake"

    def __init__(self, name: str) -> None:
        self.name = name

    async def setup(self) -> None:
        return None

    async def teardown(self) -> None:
        return None

    async def halt(self) -> None:
        return None

    async def run_task(self, envelope: dm.TaskEnvelope) -> dm.SubagentResult:
        return dm.SubagentResult(
            output={"answer": f"done-{self.name}"},
            token_usage=dm.TokenUsage(1, 2, 3),
        )


class _FakeFactory:

    def __init__(self) -> None:
        self.created: list[str] = []

    def create(self, spec: dm.AgentSpec, run: dm.Run,
                 agent_id: dm.AgentId | None = None,
                 attempt: int = 1) -> _FakeSubagent:
        self.created.append(spec.name)
        return _FakeSubagent(spec.name)


def _spawn_decision(*agents: tuple[str, str]) -> str:
    return json.dumps({
        "kind": "spawn_agent",
        "agents": [{"name": n, "objective": o} for n, o in agents],
    })


def _wait_decision(*refs: str) -> str:
    return json.dumps({"kind": "wait", "refs": list(refs), "mode": "all"})


def _finish_decision(answer: str) -> str:
    return json.dumps({"kind": "finish", "answer": answer})


def _config(**orch_overrides) -> AgentSwarmConfig:
    orch = AgentSwarmOrchestrationConfig(
        max_turns=10,
        slice_max_seconds=3.0,
        slice_interval=0.0,
        orchestrator_turn_interval=0.01,
        agent_max_attempts=1,
        batch_size=4,
    )
    if orch_overrides:
        orch = dataclasses.replace(orch, **orch_overrides)
    return AgentSwarmConfig(
        llm=LlmConfig(base_url="stub", api_key="stub", default_model="stub"),
        orchestration=orch,
        message_retention_tail=2,
    )


def _build_env(db, script: list[str]) -> tuple:
    cfg = _config()
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
        factory=_FakeFactory(),
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
    return service, worker, repos, handler


def _req(idempotency_key: str | None = None) -> SubmitRunRequest:
    return SubmitRunRequest(
        tenant=TENANT,
        goal="e2e: map the incident to owning services",
        agents=(
            dm.AgentSpec(name="a1", role="research"),
            dm.AgentSpec(name="a2", role="research"),
        ),
        idempotency_key=idempotency_key,
    )


async def _drive_to_terminal(worker: AgentSwarmWorker, run_repo: RunRepository,
                             run_id: str, max_ticks: int = 20) -> dm.Run:
    for _ in range(max_ticks):
        await worker.run_once()
        run = run_repo.get(dm.RunId(run_id), TENANT)
        if run is not None and run.status is not dm.RunStatus.RUNNING:
            return run
        await asyncio.sleep(0.005)
    return run_repo.get(dm.RunId(run_id), TENANT)


def _count_rows(db, model, tenant: str) -> int:
    with db.orm_session() as session:
        return session.execute(
            select(func.count()).select_from(model).where(
                model.tenant == tenant, model.is_deleted.is_(False),
            )
        ).scalar_one()


def _dispatch_state(db, run_id: str) -> dict:
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
        }


class TestSingleProcessEndToEnd:

    @pytest.mark.asyncio
    async def test_submit_through_worker_to_terminal_run(self, db):
        script = [
            _spawn_decision(("a1", "objective-a1"), ("a2", "objective-a2")),
            _wait_decision("a1", "a2"),
            _finish_decision("e2e final answer"),
        ]
        service, worker, repos, handler = _build_env(db, script)
        run_id = await service.submit_async(_req(idempotency_key="e2e-1"))

        run = await _drive_to_terminal(worker, repos["run"], run_id)
        assert run is not None, "run row vanished"
        assert run.status is dm.RunStatus.COMPLETED
        assert run.termination_reason == dm.TerminationReason.FINISH
        assert run.final_answer == "e2e final answer"
        assert run.ended_at is not None

        agents = repos["agent"].list_by_run(dm.RunId(run_id), TENANT)
        assert sorted(a.name.split("_", 1)[0] for a in agents) == ["a1", "a2"]
        assert all(a.status is dm.AgentStatus.COMPLETED for a in agents)
        assert all(a.attempts == 1 for a in agents)

        results = repos["result"].list_by_run(dm.RunId(run_id), TENANT)
        assert len(results) == 2
        for result in results:
            assert (result.payload is not None) != (result.artifact_key is not None)
        payloads = {repos["agent"].get(dm.RunId(run_id), s.source_agent_id, TENANT).name: s.payload
                    for s in results}
        stems = {k.split("_", 1)[0] for k in payloads}
        assert stems == {"a1", "a2"}
        for k, payload in payloads.items():
            expected = {"a1": {"answer": "done-a1"},
                       "a2": {"answer": "done-a2"}}[k.split("_", 1)[0]]
            assert payload == expected

        rounds = repos["round"].list_by_run(dm.RunId(run_id), TENANT)
        assert len(rounds) == 1
        assert len(rounds[0].cohort_agent_ids) == 2
        assert rounds[0].stage_index == 0

        assert repos["guard"].list_by_run(dm.RunId(run_id), TENANT) == []

        orchestrator_msgs = repos["message"].list_by_agent(
            dm.RunId(run_id), dm.AgentId(run_id), TENANT,
        )
        assert len(orchestrator_msgs) == 2
        assert {m.role for m in orchestrator_msgs} & {dm.MessageRole.DECISION}
        per_agent = {
            a.name: repos["message"].list_by_agent(
                dm.RunId(run_id), a.agent_id, TENANT,
            )
            for a in agents
        }
        assert all(
            len(msgs) == 1 and msgs[0].role is dm.MessageRole.ASSISTANT
            for msgs in per_agent.values()
        )

        dispatch = _dispatch_state(db, run_id)
        assert dispatch["status"] == "DONE"
        assert dispatch["claim_epoch"] >= 1
        assert dispatch["reclaims"] == 0

    @pytest.mark.asyncio
    async def test_duplicate_submit_joins_same_durable_run(self, db):
        script = [
            _spawn_decision(("a1", "objective-a1"), ("a2", "objective-a2")),
            _wait_decision("a1", "a2"),
            _finish_decision("e2e final answer"),
        ]
        service, worker, repos, handler = _build_env(db, script)
        first = await service.submit_async(_req(idempotency_key="e2e-dup"))
        second = await service.submit_async(_req(idempotency_key="e2e-dup"))
        assert first == second
        run = await _drive_to_terminal(worker, repos["run"], first)
        assert run.status is dm.RunStatus.COMPLETED

        assert _count_rows(db, om.AcAgentSwarmRunModel, TENANT) == 1
        assert _count_rows(db, om.AcAgentSwarmRunDispatchModel, TENANT) == 1
        assert _count_rows(db, om.AcAgentSwarmAgentModel, TENANT) == 2
        assert _count_rows(db, om.AcAgentSwarmAgentResultModel, TENANT) == 2
        assert _count_rows(db, om.AcAgentSwarmMessageModel, TENANT) == 4