"""Slice-level integration tests for SwarmRunHandler (task 10.7 Stage A core):
end-to-end decide -> spawn -> drain -> shard -> terminal with a real _TestDB,
scripted StubLLMProvider decisions, and a hand-fake SubagentFactory.

Covers 3.10 (shard persistence precedes terminal publish), 4.7b/D42
(schema validation), 6.1b/D24 (completed subagents never re-run),
D16/D34 (slice-lost retry + resume), D31 (shard XOR via artifact routing).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from contextlib import contextmanager

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.repository.agent import AgentRepository
from agentclaw.community.core.agentswarm.repository.guard import GuardEventRepository
from agentclaw.community.core.agentswarm.repository.job import JobRepository
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.shard import ResultShardRepository
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, AgentSwarmOrchestrationConfig,
)
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
from agentclaw.community.core.agentswarm.runtime.pool import (
    SubagentRuntime,
)
from agentclaw.community.core.agentswarm.runtime.handler import (
    SwarmRunHandler,
)

TENANT = dm.TenantId("tenant-a")
WORKER = "worker-1"


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
    # The slice path offloads repo calls via asyncio.to_thread, so the
    # engine must share one connection across threads (convention from
    # test_durable_exec / the local database plugin).
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
    yield _TestDB(engine)
    engine.dispose()


class FakeArtifactStore:
    def __init__(self) -> None:
        self.records: dict[str, str] = {}

    def put(self, key, content):
        self.records[key] = content
        return True

    def put_file(self, key, local_path):
        return False

    def delete(self, key):
        return self.records.pop(key, None) is not None

    def get(self, key):
        return self.records.get(key)


class FakeSubagent:
    def __init__(self, name: str, outputs: list, side_effects: list,
                 gate: asyncio.Event | None, tracker: dict, start: int = 0) -> None:
        self.name = name
        self._outputs = outputs
        self._side_effects = side_effects
        self._gate = gate
        self._tracker = tracker
        self._index = start

    async def setup(self) -> None:
        self._tracker["setup"] = self._tracker.get("setup", 0) + 1

    async def teardown(self) -> None:
        self._tracker["teardown"] = self._tracker.get("teardown", 0) + 1

    async def halt(self) -> None:
        await self.teardown()

    async def run_task(self, envelope: dm.TaskEnvelope) -> dm.SubagentResult:
        output = self._outputs[self._index % len(self._outputs)]
        self._side_effects.append((self.name, output))
        if self._gate is not None:
            await self._gate.wait()
        return dm.SubagentResult(output=output)


class FakeFactory:
    """Hand-fake SubagentFactory: name -> scripted outputs, create/run_tracker.

    The runtime builds a fresh subagent per attempt (fresh-context-per-attempt,
    4.4), so the per-name script cursor advances on factory.create — that is
    what makes a flaky ("first bad, then good") fake possible.
    """

    def __init__(self, scripts: dict[str, list] | None = None,
                 gate: asyncio.Event | None = None) -> None:
        self.scripts = scripts or {}
        self.creates: dict[str, int] = {}
        self.run_effects: list[tuple[str, object]] = []
        self.gate = gate
        self.tracker: dict[str, int] = {}

    def create(self, spec: dm.AgentSpec, run: dm.Run,
                 agent_id: dm.AgentId | None = None):
        seen = self.creates.get(spec.name, 0)
        self.creates[spec.name] = seen + 1
        return FakeSubagent(
            name=spec.name,
            outputs=self.scripts.get(spec.name, [{"did": spec.name}]),
            side_effects=self.run_effects,
            gate=self.gate,
            tracker=self.tracker,
            start=seen,
        )


def _now_ms() -> int:
    return int(time.time() * 1000)


def _mk_run(goal="swarm goal") -> dm.Run:
    return dm.Run(
        run_id=dm.RunId(uuid.uuid4().hex),
        tenant=TENANT,
        goal=goal,
        agent_catalog=(
            dm.AgentSpec(name="a1", role="research"),
            dm.AgentSpec(name="a2", role="research"),
            dm.AgentSpec(name="sl1", role="research"),
        ),
        provider=dm.ProviderKind("stub"),
        request_metadata=dm.RequestMetadata(),
        status=dm.RunStatus.RUNNING,
        started_at=dm.EpochMillis(_now_ms()),
    )


def _spawn_decision(name, objective, schema=None) -> str:
    agent = {"name": name, "objective": objective}
    if schema is not None:
        agent["output_schema"] = schema
    return json.dumps({"kind": "spawn_agent", "agents": [agent]})


def _wait_decision(*refs) -> str:
    return json.dumps({"kind": "wait", "refs": list(refs), "mode": "all"})


def _finish_decision(answer) -> str:
    return json.dumps({"kind": "finish", "answer": answer})


def _config(**overrides) -> AgentSwarmConfig:
    orch = AgentSwarmOrchestrationConfig(
        slice_max_seconds=8.0,
        slice_interval=0.0,
        orchestrator_turn_interval=0.01,
        subagent_max_attempts=3,
        max_width=8,
    )
    if overrides:
        import dataclasses
        orch = dataclasses.replace(orch, **overrides)
    return AgentSwarmConfig(orchestration=orch, message_retention_tail=50)


class RepeatingLLMProvider:
    """Hand-fake LLMProvider that always answers with one scripted decision
    (StubLLMProvider falls back to a default finish once its script is
    exhausted, which would terminate runs the loop needs to keep running).
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        return dm.CompletionResponse(
            text=self.text,
            model=dm.ModelId("repeat"),
            token_usage=dm.TokenUsage(),
            finish_reason=dm.FinishReason.STOP,
        )


def _build_env(db, script: list[str], factory: FakeFactory, *,
               config: AgentSwarmConfig | None = None,
               artifact_store: FakeArtifactStore | None = None,
               inline_threshold: int = 16384,
               llm=None,
               termination: TerminationEvaluator | None = None) -> SimpleNamespace:
    cfg = config or _config()
    run_repo = RunRepository(db)
    agent_repo = AgentRepository(db)
    message_repo = MessageRepository(db)
    shard_repo = ResultShardRepository(db)
    round_repo = RoundRepository(db)
    guard_repo = GuardEventRepository(db)
    job_repo = JobRepository(db)
    llm = llm or StubLLMProvider(script=script)
    orchestrator = Orchestrator(
        llm=llm,
        parser=DecisionParser(),
        guards=GuardService(max_width=cfg.orchestration.max_width,
                            max_depth=cfg.orchestration.max_depth),
        config=cfg,
    )
    termination = termination or TerminationEvaluator()
    runtime = SubagentRuntime(
        factory=factory,
        artifact_store=artifact_store,
        max_width=cfg.orchestration.max_width,
        max_attempts=cfg.orchestration.subagent_max_attempts,
        per_task_timeout=cfg.orchestration.per_subagent_timeout,
        artifact_inline_threshold=inline_threshold,
    )
    handler = SwarmRunHandler(
        config=cfg, orchestrator=orchestrator, runtime=runtime,
        run_repo=run_repo, agent_repo=agent_repo, message_repo=message_repo,
        shard_repo=shard_repo, round_repo=round_repo, guard_repo=guard_repo,
        job_repo=job_repo, termination=termination, guards=GuardService(),
    )
    return SimpleNamespace(
        cfg=cfg, run_repo=run_repo, agent_repo=agent_repo,
        message_repo=message_repo, shard_repo=shard_repo, round_repo=round_repo,
        guard_repo=guard_repo, job_repo=job_repo, llm=llm,
        orchestrator=orchestrator, runtime=runtime, handler=handler,
    )


def _enqueue_and_claim(env, run: dm.Run) -> dict:
    env.run_repo.create(run)
    env.job_repo.enqueue(run_id=run.run_id, tenant=TENANT, deadline_seconds=3600)
    return env.job_repo.claim_batch(WORKER, [TENANT], 1, 60)[0]


def _job_row(db, run_id):
    with db.orm_session() as session:
        row = session.execute(
            select(om.AcAgentSwarmJobModel).where(
                om.AcAgentSwarmJobModel.run_id == run_id,
            )
        ).scalar_one()
        return {
            "status": row.status,
            "claim_epoch": row.claim_epoch,
            "reclaims": row.reclaims,
            "last_error": row.last_error,
        }


class TestEndToEndSlice:
    def test_spawn_wait_finish_converges_with_full_persistence(self, db):
        script = [
            _spawn_decision("a1", "goal-a1", {"type": "object"}),
            _spawn_decision("a2", "goal-a2"),
            _wait_decision("a1", "a2"),
            _finish_decision("the answer"),
        ]
        store = FakeArtifactStore()
        factory = FakeFactory(scripts={
            "a1": [{"n": 1}],
            "a2": [{"blob": "x" * 80}],
        })
        env = _build_env(db, script, factory, artifact_store=store,
                         inline_threshold=20)
        run = _mk_run()
        claim = _enqueue_and_claim(env, run)

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))

        assert outcome == "complete"
        stored = env.run_repo.get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.COMPLETED
        assert stored.termination_reason == dm.TerminationReason.FINISH
        assert stored.final_answer == "the answer"
        assert stored.ended_at is not None

        shards = env.shard_repo.list_by_run(run.run_id, TENANT)
        assert len(shards) == 2
        agents = env.agent_repo.list_by_run(run.run_id, TENANT)
        assert all(s.payload is None or s.artifact_key is None for s in shards)
        assert all(s.payload is not None or s.artifact_key is not None for s in shards)
        inline = next(s for s in shards if s.payload is not None)
        assert inline.payload == {"n": 1}
        spilled = next(s for s in shards if s.artifact_key is not None)
        assert spilled.artifact_key in store.records
        assert all(
            agent.status is dm.AgentStatus.COMPLETED
            for agent in agents
        )
        assert all(agent.attempts == 1 for agent in agents)

        rounds = env.round_repo.list_by_run(run.run_id, TENANT)
        assert [r.stage_index for r in rounds] == [0, 1]
        assert rounds[0].stage_index < rounds[1].stage_index
        assert len(rounds[0].cohort_agent_ids) == 1
        assert len(rounds[1].cohort_agent_ids) == 1
        assert {c for r in rounds for c in r.cohort_agent_ids} == {
            a.agent_id for a in agents
        }

        decision_msgs = env.message_repo.list_by_agent(
            run.run_id, dm.AgentId(run.run_id), TENANT,
        )
        assert [m.role for m in decision_msgs] == (
            [dm.MessageRole.USER, dm.MessageRole.DECISION] * 4
        )
        decisions = [m.content for m in decision_msgs
                     if m.role == dm.MessageRole.DECISION]
        assert [d["kind"] for d in decisions] == [
            "spawn_agent", "spawn_agent", "wait", "finish",
        ]
        for agent in agents:
            history = env.message_repo.list_by_agent(
                run.run_id, agent.agent_id, TENANT,
            )
            assert len(history) == 1
            assert history[0].role is dm.MessageRole.ASSISTANT

        assert env.guard_repo.list_by_run(run.run_id, TENANT) == []
        job = _job_row(db, run.run_id)
        assert job["status"] == "SUCCEEDED"

    def test_wait_any_satisfied_by_single_shard(self, db):
        script = [
            _spawn_decision("a1", "goal-any-1"),
            _spawn_decision("a2", "goal-any-2"),
            _wait_decision_shorthand_any("a1", "a2"),
            _finish_decision("any done"),
        ]
        factory = FakeFactory()
        env = _build_env(db, script, factory)
        run = _mk_run()
        claim = _enqueue_and_claim(env, run)

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))
        assert outcome == "complete"
        assert env.run_repo.get(run.run_id, TENANT).final_answer == "any done"


def _wait_decision_shorthand_any(*refs) -> str:
    return json.dumps({"kind": "wait", "refs": list(refs), "mode": "any"})


class TestCongestedBatchNoProgress:
    def test_rejected_batches_continue_until_evaluator_declares_no_progress(self, db):
        # P0-8 contract: a fully guard-rejected batch (here: dedupe, the
        # spawned objective already exists) never terminalizes the run at
        # the first rejection; the slice keeps deciding and only the
        # turn-based no-progress evaluator (flat routed-shard streak)
        # ends the run.
        factory = FakeFactory()
        llm = RepeatingLLMProvider(_spawn_decision("a1", "goal-cong"))
        env = _build_env(db, [], factory, llm=llm)
        run = _mk_run()
        claim = _enqueue_and_claim(env, run)

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))

        assert outcome == "complete"
        stored = env.run_repo.get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.FAILED
        assert stored.termination_reason is dm.TerminationReason.NO_PROGRESS
        dedupes = [
            g for g in env.guard_repo.list_by_run(run.run_id, TENANT)
            if g.kind is dm.GuardKind.DEDUPE_REJECT
        ]
        assert len(dedupes) >= 3
        # the first accepted spawn completed and routed its shard
        agents = env.agent_repo.list_by_run(run.run_id, TENANT)
        assert len(agents) == 1
        assert agents[0].status is dm.AgentStatus.COMPLETED
        assert env.shard_repo.list_by_run(run.run_id, TENANT)


class TestSliceLostMidFlight:
    def test_no_shard_no_terminate_job_pending_resume_ok(self, db):
        script = [_spawn_decision("sl1", "goal-sl1"), _wait_decision("sl1")]
        gate = asyncio.Event()
        factory = FakeFactory(scripts={"sl1": [{"blocked": True}]}, gate=gate)
        env = _build_env(db, script, factory)
        run = _mk_run()
        claim = _enqueue_and_claim(env, run)

        async def main():
            task = asyncio.create_task(env.handler.handle_slice(
                run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
            ))
            for _ in range(200):
                if factory.creates.get("sl1"):
                    break
                await asyncio.sleep(0.01)
            env.handler.set_slice_lost(True, run.run_id)
            outcome = await task
            assert outcome == "retry"

        asyncio.run(main())

        assert env.shard_repo.list_by_run(run.run_id, TENANT) == []
        agents = env.agent_repo.list_by_run(run.run_id, TENANT)
        assert len(agents) == 1
        assert agents[0].status is not dm.AgentStatus.COMPLETED
        job = _job_row(db, run.run_id)
        assert job["status"] == "PENDING"
        assert env.run_repo.get(run.run_id, TENANT).status is dm.RunStatus.RUNNING

        gate.set()
        resume_script = [_wait_decision("sl1"), _finish_decision("resumed")]
        env.llm.set_script(resume_script)
        reclaimer = env.job_repo.claim_batch(WORKER, [TENANT], 1, 60)[0]
        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, reclaimer["claim_epoch"], worker_id=WORKER,
        ))
        assert outcome == "complete"
        shards = env.shard_repo.list_by_run(run.run_id, TENANT)
        assert len(shards) == 1
        stored = env.run_repo.get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.COMPLETED
        assert stored.final_answer == "resumed"
        resumed_agent = env.agent_repo.get(run.run_id, shards[0].source_agent_id,
                                           TENANT)
        assert resumed_agent.status is dm.AgentStatus.COMPLETED
        assert resumed_agent.agent_id == agents[0].agent_id


class TestOutputSchemaValidation:
    def test_schema_invalid_output_retried_then_completed(self, db):
        script = [
            _spawn_decision("a1", "goal-flaky", {"type": "object"}),
            _wait_decision("a1"),
            _finish_decision("schema ok"),
        ]
        factory = FakeFactory(scripts={"a1": ["not an object", {"ok": True}]})
        env = _build_env(db, script, factory)
        run = _mk_run()
        claim = _enqueue_and_claim(env, run)

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))
        assert outcome == "complete"
        shards = env.shard_repo.list_by_run(run.run_id, TENANT)
        assert len(shards) == 1
        assert shards[0].payload == {"ok": True}
        agent = env.agent_repo.get(run.run_id, shards[0].source_agent_id, TENANT)
        assert agent.status is dm.AgentStatus.COMPLETED
        assert agent.attempts == 2
        assert factory.run_effects.count(("a1", "not an object")) == 1
        assert factory.run_effects.count(("a1", {"ok": True})) == 1

    def test_schema_invalid_output_fails_after_attempts(self, db):
        script = [
            _spawn_decision("a1", "goal-bad-schema", {"type": "object"}),
            _wait_decision("a1"),
            _finish_decision("never reached"),
        ]
        factory = FakeFactory(scripts={"a1": ["still not an object"]})
        config = _config(subagent_max_attempts=2, slice_max_seconds=0.3)
        env = _build_env(db, script, factory, config=config)
        run = _mk_run()
        claim = _enqueue_and_claim(env, run)

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))

        # The wait on the failed agent cannot be satisfied within the slice
        # budget; the slice reschedules (never terminal) and the failed result
        # plus its error are already durable.
        assert outcome == "reschedule"
        agents = env.agent_repo.list_by_run(run.run_id, TENANT)
        assert len(agents) == 1
        assert agents[0].status is dm.AgentStatus.FAILED
        assert agents[0].attempts == 2
        assert agents[0].error.startswith("schema_validation:")
        assert env.shard_repo.list_by_run(run.run_id, TENANT) == []
        assert env.run_repo.get(run.run_id, TENANT).status is dm.RunStatus.RUNNING

    def test_malformed_schema_is_non_retryable(self, db):
        # A string schema is rejected at the parse boundary (repair-able);
        # this pins the runtime's own D42 layer via a dict schema that is
        # still invalid jsonschema — it parses, then fails check_schema.
        script = [
            _spawn_decision("a1", "goal-malformed", {"type": 123}),
            _wait_decision("a1"),
        ]
        factory = FakeFactory(scripts={"a1": [{"anything": 1}]})
        env = _build_env(db, script, factory,
                         config=_config(subagent_max_attempts=3,
                                        slice_max_seconds=0.3))
        run = _mk_run()
        claim = _enqueue_and_claim(env, run)

        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))
        assert outcome == "reschedule"
        agents = env.agent_repo.list_by_run(run.run_id, TENANT)
        assert agents[0].status is dm.AgentStatus.FAILED
        assert agents[0].error == "envelope_schema_invalid"
        assert agents[0].attempts == 1
        # The executor never even ran: a malformed envelope is a config error.
        assert factory.run_effects == []


class TestLoopStaysLiveDuringSlowWrites:
    """2.1 offload contract: slice-path repo calls run in a to_thread worker,
    so the worker loop keeps heartbeating/drain-checking while a write is
    blocked mid-DB. If a repo call ever moves back onto the loop thread,
    the ticker below starves for the whole blocked write and this fails.
    """

    def test_ticker_keeps_ticking_while_a_repo_write_blocks(self, db):
        script = [
            _spawn_decision("a1", "goal-liveness"),
            _wait_decision("a1"),
            _finish_decision("liveness done"),
        ]
        factory = FakeFactory(scripts={"a1": [{"ok": True}]})
        env = _build_env(db, script, factory)
        run = _mk_run()
        claim = _enqueue_and_claim(env, run)

        original_create = env.agent_repo.create
        write_started = threading.Event()

        def slow_create(agent):
            write_started.set()
            time.sleep(1.5)
            return original_create(agent)

        env.agent_repo.create = slow_create

        async def main():
            ticks: list[float] = []
            stop = asyncio.Event()

            async def ticker():
                while not stop.is_set():
                    ticks.append(time.monotonic())
                    await asyncio.sleep(0.05)

            slice_task = asyncio.create_task(env.handler.handle_slice(
                run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
            ))
            ticker_task = asyncio.create_task(ticker())
            while not write_started.is_set():
                await asyncio.sleep(0.005)
            # The slow agent-row write is now blocking its to_thread worker
            # for 1.5s; watch the loop tick across that window.
            block_started = time.monotonic()
            await asyncio.sleep(1.0)
            stopped = time.monotonic()
            stop.set()
            outcome = await slice_task
            await ticker_task
            return outcome, [t for t in ticks if block_started < t < stopped]

        outcome, ticks_during_blocked_write = asyncio.run(main())

        assert outcome == "complete"
        assert run.run_id and env.agent_repo.list_by_run(run.run_id, TENANT)
        # ~0.05s cadence over a 1.0s window of a still-blocked write: a
        # stalled loop (the pre-offload behavior) records zero ticks.
        assert len(ticks_during_blocked_write) >= 5