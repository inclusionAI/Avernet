"""Coverage-gap tests for the agentswarm module (OpenSpec 10.0/10.10).

Targeted unit tests for lines the main suites leave uncovered: artifact
store, subagent factory/LLM subagent, decision parser error paths,
termination evaluator branches, repository enum-decode fallbacks,
SliceStore edge cases, worker lifecycle (startup/shutdown/loop/heartbeat/
failure accounting), SwarmRunHandler cancel/retry/f fencing branches,
submit_sync polling, and runtime teardown/drain edges.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import time
import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, AgentSwarmOrchestrationConfig,
    LlmConfig,
)
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.orchestration.orchestrator import (
    Orchestrator, decision_to_dict,
)
from agentclaw.community.core.agentswarm.orchestration.guards import GuardService
from agentclaw.community.core.agentswarm.orchestration.state import RunState
from agentclaw.community.core.agentswarm.orchestration.termination import (
    TerminationEvaluator,
)
from agentclaw.community.plugins.community.agentswarm.avernet_subagent import (
    AvernetSubagent,
)
from agentclaw.community.plugins.local.agentswarm.factory import (
    DefaultSubagentFactory,
)
from agentclaw.community.plugins.local.agentswarm.subagent_llm import (
    LLMBackedSubagent,
)
from agentclaw.community.plugins.local.agentswarm.artifacts import (
    FilesystemArtifactStore,
)
from agentclaw.community.core.agentswarm.orchestration.decision_parser import (
    DecisionParser,
)
from agentclaw.community.plugins.local.agentswarm.llm_stub import StubLLMProvider
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.repository.decode import _decode_agent_status
from agentclaw.community.core.agentswarm.repository.decode import _decode_bot_state
from agentclaw.community.core.agentswarm.repository.decode import _decode_finish_reason
from agentclaw.community.core.agentswarm.repository.decode import _decode_guard_kind
from agentclaw.community.core.agentswarm.repository.decode import _decode_message_role
from agentclaw.community.core.agentswarm.repository.decode import _decode_run_status
from agentclaw.community.core.agentswarm.repository.decode import _decode_termination_reason
from agentclaw.community.core.agentswarm.repository.run.impl import _dumps_valid_json
from agentclaw.community.core.agentswarm.repository.agent import AgentRepository
from agentclaw.community.core.agentswarm.repository.guard import GuardEventRepository
from agentclaw.community.core.agentswarm.repository.job import JobRepository
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.shard import ResultShardRepository
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.core.agentswarm.repository.types import _dumps, _loads
from agentclaw.community.core.agentswarm.runtime.store import (
    SliceStore, agent_output,
)
from agentclaw.community.core.agentswarm.runtime.pool import (
    SubagentRuntime,
)
from agentclaw.community.core.agentswarm.runtime.handler import SwarmRunHandler
from agentclaw.community.core.agentswarm.runtime.worker import AgentSwarmWorker
from agentclaw.community.core.agentswarm.service import AgentSwarmService
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentSwarmSyncTimeoutError, SubmitRunRequest,
)

TENANT = dm.TenantId("tenant-a")
WORKER = "worker-gap"


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


def _engine_db() -> _TestDB:
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
    return _TestDB(engine)


class TestFilesystemArtifactStore:

    def test_put_get_roundtrip_bytes_and_text(self, tmp_path):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        assert store.put("run1/a1/result", b"payload-bytes")
        assert store.get("run1/a1/result") == b"payload-bytes"
        assert store.put("run1/a1/notes", "text-payload")
        assert store.get("run1/a1/notes") == b"text-payload"

    def test_get_missing_key_returns_none(self, tmp_path):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        assert store.get("nope/missing") is None

    def test_delete_existing_and_missing(self, tmp_path):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        store.put("k", b"v")
        assert store.delete("k") is True
        assert store.delete("k") is False

    def test_put_file_copies_source_and_missing_source_fails(self, tmp_path):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        source = tmp_path / "src.bin"
        source.write_bytes(b"file-bytes")
        assert store.put_file("copied", str(source))
        assert store.get("copied") == b"file-bytes"
        assert store.put_file("ghost", str(tmp_path / "missing.bin")) is False

    def test_key_escape_outside_root_is_rejected(self, tmp_path):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        with pytest.raises(ValueError):
            store.put("../escape", b"x")


class TestDefaultSubagentFactory:

    @pytest.mark.asyncio
    async def test_llm_kind_creates_llm_backed_subagent(self):
        factory = DefaultSubagentFactory(StubLLMProvider())
        run = _mk_run()
        spec = dm.AgentSpec(name="s1", role="research", kind=dm.AgentKind.LLM)
        sub = factory.create(spec, run, dm.AgentId("agent-s1"))
        assert isinstance(sub, LLMBackedSubagent)
        await sub.teardown()

    @pytest.mark.asyncio
    async def test_avernet_kind_creates_avernet_subagent(self):
        factory = DefaultSubagentFactory(StubLLMProvider(), platform=object())
        # Bots provision under the run's authenticated caller (P0-9
        # no-service-account stance); a callerless run fails clear at
        # spawn time, so the create-path battery carries an owner.
        run = _mk_run()
        run.owner_id = "staff-7"
        sub = factory.create(
            dm.AgentSpec(name="b1", role="worker", kind=dm.AgentKind.BOT),
            run,
            dm.AgentId("agent-b1"),
        )
        assert isinstance(sub, AvernetSubagent)

    def test_avernet_kind_without_platform_fails_clear(self):
        from agentclaw.community.core.agentswarm.service.protocols import (
            AgentSwarmConfigError,
        )

        factory = DefaultSubagentFactory(StubLLMProvider())
        with pytest.raises(AgentSwarmConfigError, match="BotPlatform"):
            factory.create(
                dm.AgentSpec(name="b1", role="worker", kind=dm.AgentKind.BOT),
                _mk_run(),
                dm.AgentId("agent-b1"),
            )


class TestLLMBackedSubagent:

    def _env(self):
        llm = StubLLMProvider(script=['{"a": 1}', "plain-text-not-json"])
        run = _mk_run()
        spec = dm.AgentSpec(name="s1", role="research", model="m-1")
        return LLMBackedSubagent(spec, run, llm), llm

    @pytest.mark.asyncio
    async def test_run_task_parses_json_response(self):
        sub, _ = self._env()
        envelope = dm.TaskEnvelope(
            name="s1", objective="do-thing", output_schema={},
            boundaries="", parent_goal="g",
        )
        result = await sub.run_task(envelope)
        assert result.output == {"a": 1}
        assert result.token_usage is not None

    @pytest.mark.asyncio
    async def test_run_task_wraps_non_json_text(self):
        sub, _ = self._env()
        envelope = dm.TaskEnvelope(
            name="s1", objective="do-thing", output_schema={},
            boundaries="", parent_goal="g",
        )
        await sub.run_task(envelope)
        result = await sub.run_task(envelope)
        assert result.output == {"text": "plain-text-not-json"}

    @pytest.mark.asyncio
    async def test_lifecycle_noop(self):
        sub, _ = self._env()
        await sub.setup()
        await sub.halt()
        await sub.teardown()


class TestAvernetSubagentTeardown:

    @pytest.mark.asyncio
    async def test_teardown_swallows_bot_delete_failure(self):
        class _BoomPlatform:
            def delete_bot(self, bot_id):
                raise RuntimeError("platform gone")

        sub = AvernetSubagent(
            dm.AgentSpec(name="b", role="worker"), _mk_run(),
            _BoomPlatform(),
            dm.AgentId("agent-b"),
        )
        sub._bot_id = "bot-1"
        await sub.teardown()
        assert sub._bot_id is None

    def test_factory_fails_clear_without_platform(self):
        from agentclaw.community.plugins.local.agentswarm.factory import (
            DefaultSubagentFactory,
        )
        from agentclaw.community.core.agentswarm.service.protocols import (
            AgentSwarmConfigError,
        )
        factory = DefaultSubagentFactory(llm=None)
        with pytest.raises(AgentSwarmConfigError, match="BotPlatform"):
            factory.create(
                dm.AgentSpec(name="b", role="worker", kind=dm.AgentKind.BOT),
                _mk_run(),
                dm.AgentId("agent-b"),
            )

    def test_factory_builds_bot_subagent_with_platform(self):
        from agentclaw.community.plugins.community.agentswarm import avernet_subagent as bot_pkg
        from agentclaw.community.plugins.local.agentswarm.factory import (
            DefaultSubagentFactory,
        )
        factory = DefaultSubagentFactory(llm=None, platform=object())
        # P0-9: bots provision under the run's authenticated caller, so
        # the create-path battery carries an owner (see factory gate).
        run = _mk_run()
        run.owner_id = "staff-7"
        sub = factory.create(
            dm.AgentSpec(name="b", role="worker", kind=dm.AgentKind.BOT),
            run,
            dm.AgentId("agent-b"),
        )
        assert isinstance(sub, bot_pkg.AvernetSubagent)


class TestDecisionParserGaps:

    def test_strips_markdown_fence_and_parses(self):
        parser = DecisionParser()
        parse = parser.parse('```json\n{"kind": "finish", "answer": "ok"}\n```')
        assert parse.decision is not None
        assert parse.decision.answer == "ok"

    def test_wait_invalid_mode_is_rejected(self):
        parse = DecisionParser().parse('{"kind": "wait", "refs": ["a"], "mode": "sometimes"}')
        assert parse.decision is None
        assert "invalid mode" in parse.error

    def test_handoff_missing_or_nonstring_target_rejected(self):
        parser = DecisionParser()
        empty = parser.parse('{"kind": "handoff", "target": ""}')
        assert empty.decision is None and "target" in empty.error
        nonstr = parser.parse('{"kind": "handoff", "target": 12}')
        assert nonstr.decision is None and "target" in nonstr.error

    def test_spawn_rejects_non_list_and_non_dict_agents(self):
        parser = DecisionParser()
        nonlist = parser.parse('{"kind": "spawn_agent", "agents": "a1"}')
        assert nonlist.decision is None and "non-empty agents" in nonlist.error
        nondict = parser.parse('{"kind": "spawn_agent", "agents": ["a1"]}')
        assert nondict.decision is None and "must be a dict" in nondict.error

    def test_spawn_rejects_missing_name_or_objective(self):
        parser = DecisionParser()
        no_name = parser.parse(
            '{"kind": "spawn_agent", "agents": [{"objective": "o"}]}'
        )
        assert no_name.decision is None and "name" in no_name.error
        no_obj = parser.parse('{"kind": "spawn_agent", "agents": [{"name": "a"}]}')
        assert no_obj.decision is None and "objective" in no_obj.error


class TestTerminationEvaluatorGaps:

    def test_missing_run_is_error(self):
        reason = TerminationEvaluator().evaluate(RunState(run=None))
        assert reason == dm.TerminationReason.ERROR

    def test_turn_budget_exhaustion(self):
        state = RunState(run=_mk_run())
        state.turn_count = 10
        reason = TerminationEvaluator(max_turns=10).evaluate(state)
        assert reason == dm.TerminationReason.TURN_BUDGET

    def test_wall_clock_expiry(self):
        run = _mk_run()
        run.started_at = dm.EpochMillis(0)
        reason = TerminationEvaluator(wall_clock_seconds=0.0).evaluate(
            RunState(run=run),
        )
        assert reason == dm.TerminationReason.WALL_CLOCK

    def test_max_agents_reached(self):
        state = RunState(run=_mk_run())
        state.total_agents_spawned = 4
        reason = TerminationEvaluator(max_total_agents=4).evaluate(state)
        assert reason == dm.TerminationReason.MAX_AGENTS


class TestRepositoryDecoders:

    def test_enum_decode_fallbacks(self):
        assert _decode_agent_status("WAT") is dm.AgentStatus.FAILED
        assert _decode_run_status("WAT") is dm.RunStatus.FAILED
        assert _decode_termination_reason("WAT") is dm.TerminationReason.ERROR
        assert _decode_termination_reason(None) is None
        assert _decode_bot_state(None) is None
        assert _decode_bot_state("WAT") is None
        assert _decode_finish_reason("WAT") is dm.FinishReason.ERROR
        assert _decode_finish_reason(None) is None
        assert _decode_guard_kind("WAT") is dm.GuardKind.KILL
        assert _decode_message_role("WAT") is dm.MessageRole.SYSTEM

    def test_dumps_loads_helpers(self):
        assert _dumps(None) is None
        assert _loads(None) is None
        assert _dumps_valid_json({"a": 1}) == '{"a": 1}'


class TestSliceStoreGaps:

    @pytest.fixture()
    def db(self):
        return _engine_db()

    def _store(self, db, lost_probe=lambda: False,
               config: AgentSwarmConfig | None = None) -> SliceStore:
        cfg = config or AgentSwarmConfig()
        return SliceStore(
            cfg, AgentRepository(db), MessageRepository(db),
            ResultShardRepository(db), RoundRepository(db), lost_probe,
        )

    def _mk_run(self, db) -> dm.Run:
        run = _mk_run()
        RunRepository(db).create(run)
        return run

    @staticmethod
    def _claim_job(db, run: dm.Run) -> int:
        # persist_completion is claim-epoch fenced (D28): its agent-status
        # write only lands on the CURRENT job epoch, so the test must stand
        # up the durable job row a real slice would hold.
        job_repo = JobRepository(db)
        job_repo.enqueue(run_id=run.run_id, tenant=run.tenant,
                         deadline_seconds=3600)
        return job_repo.claim_batch(
            WORKER, [run.tenant], 1, 60,
        )[0]["claim_epoch"]

    @staticmethod
    def _completion(run: dm.Run, agent_id: dm.AgentId, status: dm.AgentStatus,
                    result=None, artifact_key=None, error=None) -> dict:
        return {
            "run_id": run.run_id, "tenant": run.tenant,
            "agent_id": agent_id, "status": status, "result": result,
            "artifact_key": artifact_key, "error": error,
            "attempts": 1, "token_usage": dm.TokenUsage(1, 1, 2),
        }

    def test_agent_output_wraps_non_dict_payloads(self):
        assert agent_output({"a": 1}, None) == {"a": 1}
        assert agent_output("raw-text", None) == {"value": "raw-text"}
        assert agent_output(None, "art-key") == {"artifact_key": "art-key"}
        assert agent_output(None, None) == {}

    def test_next_seq_strictly_increases_within_same_milli(self, db):
        store = self._store(db)
        store._last_seq = poisoned = int(time.time() * 1000) + 5000
        seq = store.next_seq()
        assert seq == poisoned + 1
        assert store._last_seq == seq

    def test_persist_completion_respects_lost_probe(self, db):
        db_probe = self._store(db, lost_probe=lambda: True)
        run = self._mk_run(db)
        agent = _mk_agent(run, "a1")
        AgentRepository(db).create(agent)
        asyncio.run(db_probe.persist_completion(
            self._completion(run, agent.agent_id, dm.AgentStatus.COMPLETED,
                             result={"r": 1}),
            claim_epoch=0,
        ))
        stored = AgentRepository(db).get(run.run_id, agent.agent_id, TENANT)
        assert stored.status is dm.AgentStatus.QUEUED

    def test_persist_completion_refuses_malformed_shard_xor(self, db):
        store = self._store(db)
        run = self._mk_run(db)
        agent = _mk_agent(run, "a1")
        AgentRepository(db).create(agent)
        asyncio.run(store.persist_completion(
            self._completion(run, agent.agent_id, dm.AgentStatus.COMPLETED),
            claim_epoch=self._claim_job(db, run),
        ))
        shard = ResultShardRepository(db).get_by_run_agent(
            run.run_id, agent.agent_id, TENANT,
        )
        assert shard is None

    def test_persist_completion_failed_error_message(self, db):
        store = self._store(db)
        run = self._mk_run(db)
        agent = _mk_agent(run, "a1")
        AgentRepository(db).create(agent)
        asyncio.run(store.persist_completion(
            self._completion(run, agent.agent_id, dm.AgentStatus.FAILED,
                             error="boom"),
            claim_epoch=self._claim_job(db, run),
        ))
        messages = MessageRepository(db).list_by_agent(
            run.run_id, agent.agent_id, TENANT,
        )
        (message,) = messages
        assert message.content == {"status": "FAILED", "error": "boom"}

    def test_persist_completion_artifact_carried_shard(self, db):
        store = self._store(db)
        run = self._mk_run(db)
        agent = _mk_agent(run, "a1")
        AgentRepository(db).create(agent)
        asyncio.run(store.persist_completion(
            self._completion(run, agent.agent_id, dm.AgentStatus.COMPLETED,
                             artifact_key="run/artifact"),
            claim_epoch=self._claim_job(db, run),
        ))
        shard = ResultShardRepository(db).get_by_run_agent(
            run.run_id, agent.agent_id, TENANT,
        )
        assert shard is not None and shard.payload is None
        assert shard.artifact_key == "run/artifact"
        (message,) = MessageRepository(db).list_by_agent(
            run.run_id, agent.agent_id, TENANT,
        )
        assert message.content == {"status": "COMPLETED", "artifact_key": "run/artifact"}

    def test_rehydrate_restores_persona_from_handoff_decisions(self, db):
        store = self._store(db)
        run = self._mk_run(db)
        store.record_llm_exchange(
            run.run_id, TENANT, "prompt", {"kind": "handoff", "target": "researcher"},
            dm.TokenUsage(),
        )
        state, done_pairs, interrupted = asyncio.run(store.rehydrate(run))
        assert state.turn_count == 1
        assert state.active_persona is not None
        assert state.active_persona.role == "researcher"
        assert done_pairs == set()
        assert interrupted == []


class TestSubmitSync:

    @pytest.fixture()
    def db(self):
        return _engine_db()

    def _service(self, db, config: AgentSwarmConfig) -> tuple:
        repos = self._repos(db)
        service = AgentSwarmService(
            run_repo=repos["run"], job_repo=repos["job"],
            agent_repo=repos["agent"], message_repo=repos["message"],
            round_repo=repos["round"], guard_repo=repos["guard"],
            shard_repo=repos["shard"], config=config,
        )
        return service, repos

    @pytest.mark.asyncio
    async def test_submit_sync_returns_when_run_turns_terminal_mid_poll(self, db):
        service, repos = self._service(
            db, AgentSwarmConfig(llm=LlmConfig(base_url="stub")),
        )
        req = _req(idempotency_key="sync-1")
        run_id = await service.submit_async(req)
        (claimed,) = repos["job"].claim_batch(WORKER, [TENANT], 1, 60)
        epoch = claimed["claim_epoch"]

        async def flip_terminal():
            await asyncio.sleep(0.05)
            repos["run"].update_status(
                dm.RunId(run_id), TENANT, dm.RunStatus.COMPLETED,
                termination_reason=dm.TerminationReason.FINISH,
                final_answer="done", claim_epoch=epoch,
            )

        flip = asyncio.create_task(flip_terminal())
        synced = await service.submit_sync(req)
        await flip
        assert synced.status is dm.RunStatus.COMPLETED
        assert synced.final_answer == "done"
        assert synced.run_id == run_id

    @pytest.mark.asyncio
    async def test_submit_sync_deadline_of_deleted_run_raises_typed_error(self, db):
        from sqlalchemy import update as _update
        config = AgentSwarmConfig(
            llm=LlmConfig(base_url="stub"),
            orchestration=AgentSwarmOrchestrationConfig(slice_max_seconds=1.0),
        )
        service, repos = self._service(db, config)
        req = _req(idempotency_key="sync-vanish")
        run_id = await service.submit_async(req)

        async def delete_mid_poll():
            await asyncio.sleep(0.05)
            with db.orm_session() as session:
                session.execute(
                    _update(om.AcAgentSwarmRunModel).where(
                        om.AcAgentSwarmRunModel.run_id == dm.RunId(run_id),
                    ).values(is_deleted=True)
                )

        deleter = asyncio.create_task(delete_mid_poll())
        # A run deleted mid-wait is truly gone at expiry: submit_sync must
        # refuse to fabricate a TIMED_OUT Run and raise the typed error.
        with pytest.raises(AgentSwarmSyncTimeoutError) as caught:
            await service.submit_sync(req)
        await deleter
        assert caught.value.run_id == run_id
        assert caught.value.deadline == 1.0
        assert service.get_run(run_id, TENANT) is None

    def _repos(self, db) -> dict:
        return {
            "run": RunRepository(db), "job": JobRepository(db),
            "agent": AgentRepository(db), "round": RoundRepository(db),
            "guard": GuardEventRepository(db),
            "message": MessageRepository(db),
            "shard": ResultShardRepository(db),
        }


class TestWorkerLifecycle:

    def _config(self, *, enabled: bool = True, **orch_overrides) -> AgentSwarmConfig:
        orch = AgentSwarmOrchestrationConfig(
            poll_interval=0.01, poll_jitter=0.0, lease_seconds=0.03,
            slice_max_seconds=1.0, slice_interval=0.0, batch_size=1,
        )
        if orch_overrides:
            orch = dataclasses.replace(orch, **orch_overrides)
        return AgentSwarmConfig(orchestration=orch, enabled=enabled)

    def test_disabled_worker_does_not_start(self):
        worker = AgentSwarmWorker(
            config=self._config(enabled=False),
            handler=StubHandler(), job_repo=StubJobRepo(),
        )
        asyncio.run(worker.startup())
        assert worker._running is False
        assert worker._task is None

    @pytest.mark.asyncio
    async def test_startup_then_shutdown_drains(self):
        worker = AgentSwarmWorker(
            config=self._config(), handler=StubHandler(),
            job_repo=StubJobRepo(claim=[_job_dict()]),
        )
        await worker.startup()
        assert worker._running is True
        await asyncio.sleep(0.03)
        assert worker._worker_id
        await worker.shutdown()
        assert worker._running is False
        assert worker._task.done() and worker._heartbeat_task.done()

    @pytest.mark.asyncio
    async def test_loop_survives_poll_exceptions(self):
        repo = StubJobRepo()
        repo.reclaim_error = ValueError("reclaim exploded")
        worker = AgentSwarmWorker(
            config=self._config(), handler=StubHandler(), job_repo=repo,
        )
        await worker.startup()
        await asyncio.sleep(0.05)
        assert worker._running is True
        await worker.shutdown()

    @pytest.mark.asyncio
    async def test_heartbeat_lease_loss_marks_slice_lost_and_pops_inflight(self):
        handler = StubHandler()
        worker = AgentSwarmWorker(
            config=self._config(), handler=handler, job_repo=StubJobRepo(),
        )
        worker._inflight["run-x"] = {"tenant": TENANT, "claim_epoch": 1}
        worker._running = True
        heartbeat = asyncio.create_task(worker._heartbeat_loop())
        await asyncio.sleep(0.1)
        worker._running = False
        await heartbeat
        assert ("set-slice-lost", "run-x") in handler.calls
        assert "run-x" not in worker._inflight

    @pytest.mark.asyncio
    async def test_run_job_failure_force_fails_the_run_and_clears_inflight(self):
        handler = StubHandler(slice_error=ValueError("slice gone wrong"))
        repo = StubJobRepo()
        worker = AgentSwarmWorker(
            config=self._config(), handler=handler, job_repo=repo,
        )
        job = _job_dict()
        await worker._run_job(job, reclaimed=False)
        assert handler.calls == [
            ("handle-slice", "run-1", 1),
            ("force-fail", "run-1", 1, "slice gone wrong"),
        ]
        assert repo.fails == []
        assert "run-1" not in worker._inflight


def _job_dict(run_id="run-1", epoch=1, reclaims=0, cancel=False) -> dict:
    return {
        "run_id": dm.RunId(run_id), "tenant": TENANT, "claim_epoch": epoch,
        "reclaims": reclaims, "cancel_requested": cancel,
    }


class StubHandler:

    def __init__(self, slice_error: Exception | None = None) -> None:
        self.calls: list = []
        self._slice_error = slice_error

    def set_slice_lost(self, lost: bool, run_id=None) -> None:
        self.calls.append(("set-slice-lost", run_id))

    async def handle_slice(self, run_id, tenant, claim_epoch,
                           cancel_requested=False, *, worker_id=None):
        self.calls.append(("handle-slice", run_id, claim_epoch))
        if self._slice_error is not None:
            raise self._slice_error
        return "complete"

    def force_stuck(self, run_id, tenant, epoch, error, worker_id=None):
        self.calls.append(("force-stuck", run_id))
        return True

    def force_fail(self, run_id, tenant, epoch, error, worker_id=None):
        self.calls.append(("force-fail", run_id, epoch, error))
        return True


class StubJobRepo:

    def __init__(self, claim: list | None = None) -> None:
        self.claim = claim or []
        self.reclaim_error: Exception | None = None
        self.fails: list = []

    def reclaim_batch(self, worker_id, limit, lease_seconds):
        if self.reclaim_error is not None:
            raise self.reclaim_error
        return []

    def claim_batch(self, worker_id, tenant_filter, limit, lease_seconds):
        return self.claim

    def renew_lease(self, run_id, tenant, worker_id, lease_seconds, claim_epoch):
        return False

    def fail(self, run_id, tenant, error, worker_id, claim_epoch):
        self.fails.append((dm.RunId(run_id), tenant, error, worker_id, claim_epoch))
        return True


def _mk_run(goal="gap goal") -> dm.Run:
    return dm.Run(
        run_id=dm.RunId(uuid.uuid4().hex),
        tenant=TENANT,
        goal=goal,
        agent_catalog=(
            dm.AgentSpec(name="a1", role="research"),
            dm.AgentSpec(name="a2", role="researcher",
                            instructions="be terse"),
        ),
        provider=dm.ProviderKind("stub"),
        request_metadata=dm.RequestMetadata(),
        status=dm.RunStatus.RUNNING,
        started_at=dm.EpochMillis(int(time.time() * 1000)),
    )


def _mk_agent(run: dm.Run, name: str) -> dm.Agent:
    return dm.Agent(
        agent_id=dm.AgentId(uuid.uuid4().hex),
        run_id=run.run_id, tenant=run.tenant, parent_agent_id=None, depth=0,
        name=name, role=f"objective-{name}", kind=dm.AgentKind.LLM,
        task_spec=dm.TaskEnvelope(
            name=name, objective=f"objective-{name}", output_schema={},
            boundaries="", parent_goal=run.goal,
        ),
        spawn_round=0, status=dm.AgentStatus.QUEUED, attempts=0,
        idempotency_key=dm.IdempotencyKey(uuid.uuid4().hex), output={},
    )


def _req(idempotency_key: str | None = None) -> SubmitRunRequest:
    return SubmitRunRequest(
        tenant=TENANT,
        goal="coverage-gap goal",
        agents=(dm.AgentSpec(name="a1", role="research"),),
        idempotency_key=idempotency_key,
    )


class RepeatingLLMProvider:

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        return dm.CompletionResponse(
            text=self.text, model=dm.ModelId("repeat"),
            token_usage=dm.TokenUsage(), finish_reason=dm.FinishReason.STOP,
        )


class _InstantSubagent:

    def __init__(self, name: str) -> None:
        self.name = name

    async def setup(self) -> None:
        return None

    async def teardown(self) -> None:
        return None

    async def halt(self) -> None:
        return None

    async def run_task(self, envelope) -> dm.SubagentResult:
        return dm.SubagentResult(output={"answer": self.name})


class _RecordingFactory:

    def __init__(self) -> None:
        self.created_specs: list[str] = []

    def create(self, spec: dm.AgentSpec, run: dm.Run,
                 agent_id: dm.AgentId | None = None) -> _InstantSubagent:
        self.created_specs.append(spec.name)
        return _InstantSubagent(spec.name)


def _handler_env(db, script: list[str] | None = None, *,
                 config: AgentSwarmConfig | None = None,
                 llm=None):
    cfg = config or AgentSwarmConfig(
        llm=LlmConfig(base_url="stub"),
        orchestration=AgentSwarmOrchestrationConfig(
            orchestrator_turn_interval=0.01, slice_max_seconds=3.0,
            slice_interval=0.0, max_turns=10,
        ),
    )
    repos = {
        "run": RunRepository(db), "agent": AgentRepository(db),
        "message": MessageRepository(db), "shard": ResultShardRepository(db),
        "round": RoundRepository(db), "guard": GuardEventRepository(db),
        "job": JobRepository(db),
    }
    llm = llm or StubLLMProvider(script=script or [])
    orchestrator = Orchestrator(
        llm=llm, parser=DecisionParser(),
        guards=GuardService(max_width=cfg.orchestration.max_width,
                            max_depth=cfg.orchestration.max_depth),
        config=cfg,
    )
    runtime = SubagentRuntime(
        factory=_RecordingFactory(),
        max_width=cfg.orchestration.max_width,
        max_attempts=cfg.orchestration.subagent_max_attempts,
        per_task_timeout=cfg.orchestration.per_subagent_timeout,
        artifact_inline_threshold=cfg.artifact_inline_threshold,
    )
    handler = SwarmRunHandler(
        config=cfg, orchestrator=orchestrator, runtime=runtime,
        run_repo=repos["run"], agent_repo=repos["agent"],
        message_repo=repos["message"], shard_repo=repos["shard"],
        round_repo=repos["round"], guard_repo=repos["guard"],
        job_repo=repos["job"],
        termination=TerminationEvaluator(max_turns=cfg.orchestration.max_turns),
        guards=GuardService(),
    )
    return handler, repos, cfg


def _spawn_decision(name, objective) -> str:
    return json.dumps(
        {"kind": "spawn_agent", "agents": [{"name": name, "objective": objective}]}
    )


def _wait_decision(*refs, mode="all") -> str:
    return json.dumps({"kind": "wait", "refs": list(refs), "mode": mode})


def _finish_decision(answer) -> str:
    return json.dumps({"kind": "finish", "answer": answer})


def _handoff_decision(target) -> str:
    return json.dumps({"kind": "handoff", "target": target})


def _enqueue_claim(repos, run: dm.Run, worker: str = WORKER) -> dict:
    repos["run"].create(run)
    repos["job"].enqueue(run_id=run.run_id, tenant=TENANT, deadline_seconds=3600)
    return repos["job"].claim_batch(worker, [TENANT], 1, 60)[0]


class TestSwarmHandlerEdges:

    @pytest.fixture()
    def db(self):
        return _engine_db()

    def test_missing_run_slice_fails(self, db):
        handler, _, _ = _handler_env(db)
        outcome = asyncio.run(handler.handle_slice(dm.RunId("ghost"), TENANT, 0))
        assert outcome == "fail"

    def test_slice_on_terminal_run_just_completes(self, db):
        handler, repos, _ = _handler_env(db)
        run = _mk_run()
        run.status = dm.RunStatus.COMPLETED
        claim = _enqueue_claim(repos, run)
        outcome = asyncio.run(handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))
        assert outcome == "complete"

    def test_cancel_requested_slice_transitions_run_and_job(self, db):
        handler, repos, _ = _handler_env(db)
        run = _mk_run()
        claim = _enqueue_claim(repos, run)
        outcome = asyncio.run(handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"],
            cancel_requested=True, worker_id=WORKER,
        ))
        assert outcome == "complete"
        stored = repos["run"].get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.CANCELLED
        assert stored.termination_reason == dm.TerminationReason.CANCELLED
        assert stored.ended_at is not None

    def test_wait_any_mode_resumes_once_one_ref_satisfied(self, db):
        script = [
            _spawn_decision("a1", "objective-a1"),
            _wait_decision("a1", "ghost", mode="any"),
            _finish_decision("done via any-wait"),
        ]
        handler, repos, _ = _handler_env(db, script)
        run = _mk_run()
        claim = _enqueue_claim(repos, run)
        outcome = asyncio.run(handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))
        assert outcome == "complete"
        stored = repos["run"].get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.COMPLETED
        assert stored.final_answer == "done via any-wait"

    def test_wait_budget_exhaustion_reschedules_for_next_slice(self, db):
        config = AgentSwarmConfig(
            llm=LlmConfig(base_url="stub"),
            orchestration=AgentSwarmOrchestrationConfig(
                slice_max_seconds=0.05, slice_interval=0.0,
                orchestrator_turn_interval=0.01, max_turns=10,
            ),
        )
        handler, repos, _ = _handler_env(
            db, llm=RepeatingLLMProvider(_wait_decision("ghost")), config=config,
        )
        run = _mk_run()
        claim = _enqueue_claim(repos, run)
        outcome = asyncio.run(handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))
        assert outcome == "reschedule"
        from sqlalchemy import select as _select
        with db.orm_session() as session:
            status = session.execute(
                _select(om.AcAgentSwarmJobModel.status).where(
                    om.AcAgentSwarmJobModel.run_id == run.run_id,
                )
            ).scalar_one()
        assert status == "PENDING"
        assert repos["run"].get(run.run_id, TENANT).status is dm.RunStatus.RUNNING

    def test_force_stuck_missing_run_completes_job_and_returns_false(self, db):
        handler, repos, _ = _handler_env(db)
        assert handler.force_stuck(
            dm.RunId("ghost"), TENANT, 0, "reclaim circuit breaker", WORKER,
        ) is False

    def test_set_slice_lost_for_foreign_run_is_ignored(self, db):
        handler, _, _ = _handler_env(db)
        handler._active_run_id = dm.RunId("current")
        handler.set_slice_lost(True, "foreign-run")
        assert handler._slice_lost.is_set() is False

    def test_stale_epoch_finalize_is_fenced_into_retry(self, db):
        handler, repos, _ = _handler_env(
            db, llm=RepeatingLLMProvider(_spawn_decision("a1", "objective-a1")),
        )
        run = _mk_run()
        claim = _enqueue_claim(repos, run)
        stale_epoch = claim["claim_epoch"]
        repos["job"].renew_lease(run.run_id, TENANT, WORKER, -1, stale_epoch)
        reclaimed = repos["job"].reclaim_batch("worker-2", 10, 60)
        assert reclaimed[0]["claim_epoch"] == stale_epoch + 1

        outcome = asyncio.run(handler.handle_slice(
            run.run_id, TENANT, stale_epoch, worker_id=WORKER,
        ))
        assert outcome == "retry"
        # D28: the fenced-off FAILED transition must NOT land; the
        # reclaimer owns the outcome for the run it now leases.
        stored = repos["run"].get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.RUNNING

    def test_handoff_slice_applies_persona_then_finishes(self, db):
        script = [
            _handoff_decision("researcher"),
            _spawn_decision("a1", "objective-a1"),
            _wait_decision("a1"),
            _finish_decision("persona answer"),
        ]
        handler, repos, _ = _handler_env(db, script)
        run = _mk_run()
        claim = _enqueue_claim(repos, run)
        outcome = asyncio.run(handler.handle_slice(
            run.run_id, TENANT, claim["claim_epoch"], worker_id=WORKER,
        ))
        assert outcome == "complete"
        stored = repos["run"].get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.COMPLETED
        assert stored.final_answer == "persona answer"

    def test_resolve_final_status_covers_all_terminal_reasons(self):
        resolve = SwarmRunHandler._resolve_final_status
        assert resolve(dm.TerminationReason.FINISH) is dm.RunStatus.COMPLETED
        assert resolve(dm.TerminationReason.CANCELLED) is dm.RunStatus.CANCELLED
        assert resolve(dm.TerminationReason.WALL_CLOCK) is dm.RunStatus.TIMED_OUT
        assert resolve(dm.TerminationReason.STUCK) is dm.RunStatus.STUCK
        assert resolve(dm.TerminationReason.GUARD) is dm.RunStatus.GUARD_KILLED
        assert resolve(dm.TerminationReason.ERROR) is dm.RunStatus.FAILED
        assert resolve(dm.TerminationReason.NO_PROGRESS) is dm.RunStatus.FAILED


class TestOrchestratorEdges:

    def _orchestrator(self, llm, config: AgentSwarmConfig | None = None):
        cfg = config or AgentSwarmConfig(llm=LlmConfig(base_url="stub"))
        return Orchestrator(
            llm=llm, parser=DecisionParser(),
            guards=GuardService(), config=cfg,
        )

    def test_decision_to_dict_falls_back_for_unknown_type(self):
        assert decision_to_dict(object()) == {"kind": None}

    def test_resolve_persona_matches_role_or_none(self):
        run = _mk_run()
        found = Orchestrator._resolve_persona(run, "researcher")
        assert found is not None and found.name == "a2"
        assert Orchestrator._resolve_persona(run, "nobody") is None

    def test_system_prompt_appends_persona_instructions(self):
        orchestrator = self._orchestrator(StubLLMProvider())
        run = _mk_run()
        state = RunState(run=run)
        state.active_persona = dm.AgentSpec(
            name="a2", role="researcher", instructions="be terse",
        )
        prompt = orchestrator._system_prompt(run, state)
        assert "be terse" in prompt
        assert "You now act as a2" in prompt

    @pytest.mark.asyncio
    async def test_unresolvable_handoff_exhausts_repair_into_no_decision(self):
        orchestrator = self._orchestrator(
            RepeatingLLMProvider(_handoff_decision("nobody")),
        )
        run = _mk_run()
        state = RunState(run=run)
        outcome = await orchestrator.run_slice(run, state, None)
        assert outcome["terminate"] is dm.TerminationReason.NO_DECISION
        assert outcome["guard_events"], "unresolvable handoff must record a guard"
        # Closed-set note: GuardKind has no invalid-decision member, so the
        # event reuses the decision-reject slot; details["reason"] is the
        # authoritative discriminator (adding a dedicated kind requires
        # coordinating domain/models/enums.py).
        ge = outcome["guard_events"][0]
        assert ge.kind is dm.GuardKind.DEDUPE_REJECT
        assert ge.details["reason"] == "unresolvable_handoff"
        assert ge.details["target"] == "nobody"
        assert ge.details["attempt"] == 1
        assert state.active_persona is None


class TestRuntimeEdges:

    @pytest.fixture()
    def runtime(self):
        return SubagentRuntime(
            factory=_RecordingFactory(),
            max_width=2, max_attempts=1, per_task_timeout=0.5,
            artifact_inline_threshold=128,
        )

    @pytest.mark.asyncio
    async def test_shard_handler_receives_each_completion(self, runtime):
        seen: list[dict] = []
        runtime.set_shard_handler(seen.append)
        await runtime.start()
        await runtime.spawn(_mk_run(), _envelope("a1"))
        await runtime.wait_for_completions(timeout=0.5)
        await runtime.stop()
        assert [c["status"] for c in seen] == [dm.AgentStatus.COMPLETED]

    @pytest.mark.asyncio
    async def test_unknown_envelope_name_uses_ad_hoc_spec(self, runtime):
        await runtime.start()
        await runtime.spawn(_mk_run(), _envelope("mystery-agent"))
        await runtime.wait_for_completions(timeout=0.5)
        await runtime.stop()
        assert runtime._factory.created_specs == ["mystery-agent"]

    @pytest.mark.asyncio
    async def test_invalid_envelope_schema_fails_clearly(self, runtime):
        await runtime.start()
        envelope = dm.TaskEnvelope(
            name="s1", objective="o", output_schema={"type": "bankai"},
            boundaries="", parent_goal="g",
        )
        await runtime.spawn(_mk_run(), envelope)
        completions = await runtime.wait_for_completions(timeout=0.5)
        await runtime.stop()
        assert completions[0]["status"] is dm.AgentStatus.FAILED
        assert completions[0]["error"] == "envelope_schema_invalid"

    @pytest.mark.asyncio
    async def test_subagent_teardown_failure_is_swallowed(self):
        class FailingSubagent:
            name = "boom"

            async def setup(self):
                return None

            async def teardown(self):
                raise RuntimeError("teardown exploded")

            async def halt(self):
                return None

            async def run_task(self, envelope):
                raise RuntimeError("run exploded")

        class FailingFactory:
            def create(self, spec, run, agent_id=None):
                return FailingSubagent()

        runtime = SubagentRuntime(
            factory=FailingFactory(), max_width=2, max_attempts=2,
            per_task_timeout=0.5,
        )
        await runtime.start()
        await runtime.spawn(_mk_run(), _envelope("boom"))
        completions = await runtime.wait_for_completions(timeout=0.5)
        await runtime.stop()
        assert completions[0]["status"] is dm.AgentStatus.FAILED
        assert completions[0]["attempts"] == 2


def _envelope(name: str, schema: dict | None = None) -> dm.TaskEnvelope:
    return dm.TaskEnvelope(
        name=name, objective=f"objective-{name}",
        output_schema=schema or {}, boundaries="", parent_goal="g",
    )