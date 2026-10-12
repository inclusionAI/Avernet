"""Metrics platform seam + D39 surface tests (task 12.4).

Covers, in order of the stack:

- CommunityMetrics: bounded in-proc registry behaviors — counters
  accumulate, label maps distinguish cells, observes fold to
  count/sum/min/max, gauges keep the last value, unhashable label
  values are str-coerced (the Protocol's never-raise contract).
- NoopMetrics: silent on every operation; ``time_block`` is a usable
  context manager (the local/test column binding).
- AgentComputeObservability: every named operation routes to the
  canonical metric name + label map (recording fake plugin).
- Hooked sites: OpenAICompatibleClient outcome/token counters on the
  retry paths (httpx.MockTransport), SliceStore persist_completion
  terminal/shard counters (real SQLite _TestDB), SwarmRunHandler slice
  duration/spawn/STUCK hooks, AvernetSubagent gateway outcome counters.
- DI: the injector builds AgentComputeObservability; a profile column's
  MetricsPlugin binding overrides the agentcompute fallback default.

Conventions follow test_llm_client.py / test_swarm_slice.py: sync test
defs driving async code through asyncio.run, hand-written fakes, no
network.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import contextmanager

import httpx
import pytest
from injector import Injector, Module, provider, singleton
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentcompute.config.settings import (
    AgentComputeAvernetConfig, AgentComputeConfig, AgentComputeOrchestrationConfig,
    LlmConfig,
)
from agentclaw.community.core.agentcompute.domain import models as dm
from agentclaw.community.core.agentcompute.runtime.observability import (
    AgentComputeObservability, NULL_OBSERVABILITY, UNATTRIBUTED_TENANT,
)
from agentclaw.community.core.agentcompute.orchestration.orchestrator import (
    Orchestrator,
)
from agentclaw.community.core.agentcompute.orchestration.guards import GuardService
from agentclaw.community.core.agentcompute.orchestration.termination import (
    TerminationEvaluator,
)
from agentclaw.community.core.agentcompute.plugins.decision_parser.parser import (
    DecisionParser,
)
from agentclaw.community.core.agentcompute.plugins.agents.avernet import (
    AvernetSubagent,
)
from agentclaw.community.core.agentcompute.plugins.llm.openai import OpenAICompatibleClient
from agentclaw.community.core.agentcompute.plugins.llm.stub import StubLLMProvider
from agentclaw.community.core.agentcompute.repository import registry as om
from agentclaw.community.core.agentcompute.repository.agent import AgentRepository
from agentclaw.community.core.agentcompute.repository.guard import GuardEventRepository
from agentclaw.community.core.agentcompute.repository.job import JobRepository
from agentclaw.community.core.agentcompute.repository.message import MessageRepository
from agentclaw.community.core.agentcompute.repository.shard import ResultShardRepository
from agentclaw.community.core.agentcompute.repository.round import RoundRepository
from agentclaw.community.core.agentcompute.repository.run import RunRepository
from agentclaw.community.core.agentcompute.runtime.pool import (
    SubagentRuntime,
)
from agentclaw.community.core.agentcompute.runtime.handler import SwarmRunHandler
from agentclaw.community.core.agentcompute.runtime.store import SliceStore
from agentclaw.community.core.agentcompute.spi.llm import LLMProvider
from agentclaw.community.di.modules.agentcompute_module import AgentComputeModule
from agentclaw.community.di.modules.infrastructure.community.metrics import (
    CommunityMetricsModule,
)
from agentclaw.community.plugins.community.metrics import CommunityMetrics
from agentclaw.community.plugins.local.metrics import NoopMetrics
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.metrics import MetricsPlugin

TENANT = dm.TenantId("tenant-a")
WORKER = "worker-obs"


class RecordingMetrics(MetricsPlugin):
    """Hand fake: every operation records (op, name, value, labels)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, float | None, dict]] = []

    def increment(self, name: str, labels=None) -> None:
        self.events.append(("increment", name, None, dict(labels or {})))

    def observe(self, name: str, value: float, labels=None) -> None:
        self.events.append(("observe", name, float(value), dict(labels or {})))

    def set_gauge(self, name: str, value: float, labels=None) -> None:
        self.events.append(("set_gauge", name, float(value), dict(labels or {})))

    @contextmanager
    def time_block(self, name: str, labels=None):
        self.events.append(("time_block", name, None, dict(labels or {})))
        yield

    def of_op(self, op: str) -> list[tuple[str, str, float | None, dict]]:
        return [e for e in self.events if e[0] == op]

    def increments_of(self, name: str) -> list[dict]:
        return [e[3] for e in self.events if e[0] == "increment" and e[1] == name]


# ── CommunityMetrics registry ─────────────────────────────────────────────


class TestCommunityMetrics:
    def test_increment_accumulates_per_name(self):
        m = CommunityMetrics()
        m.increment("agentcompute_run_claim_total", labels={"kind": "claim"})
        m.increment("agentcompute_run_claim_total", labels={"kind": "claim"})
        m.increment("agentcompute_stuck_transition_total")
        snap = m.snapshot()
        assert snap["increment"][("agentcompute_run_claim_total", (("kind", "claim"),))] == 2.0
        assert snap["increment"][("agentcompute_stuck_transition_total", ())] == 1.0

    def test_label_maps_distinguish_cells(self):
        m = CommunityMetrics()
        m.increment("llm_call", labels={"outcome": "ok"})
        m.increment("llm_call", labels={"outcome": "timeout"})
        m.increment("llm_call", labels={"outcome": "ok"})
        snap = m.snapshot()
        assert snap["increment"][("llm_call", (("outcome", "ok"),))] == 2.0
        assert snap["increment"][("llm_call", (("outcome", "timeout"),))] == 1.0

    def test_label_map_order_does_not_split_cells(self):
        m = CommunityMetrics()
        m.increment("x", labels={"a": "1", "b": "2"})
        m.increment("x", labels={"b": "2", "a": "1"})
        snap = m.snapshot()
        assert snap["increment"][("x", (("a", "1"), ("b", "2")))] == 2.0

    def test_observe_folds_count_sum_min_max(self):
        m = CommunityMetrics()
        for v in (5.0, 1.0, 9.0):
            m.observe("agentcompute_llm_tokens_total", v, labels={"tenant": "t1"})
        cell = m.snapshot()["observe"][("agentcompute_llm_tokens_total", (("tenant", "t1"),))]
        assert cell == {"count": 3, "sum": 15.0, "min": 1.0, "max": 9.0}

    def test_gauge_keeps_last_value_only(self):
        m = CommunityMetrics()
        m.set_gauge("agentcompute_queue_depth", 4.0)
        m.set_gauge("agentcompute_queue_depth", 9.0)
        snap = m.snapshot()
        assert snap["set_gauge"][("agentcompute_queue_depth", ())] == 9.0
        m.set_gauge("agentcompute_queue_depth", 2.0, labels={"tenant": "other"})
        assert snap["set_gauge"][("agentcompute_queue_depth", ())] == 9.0

    def test_time_block_records_nonnegative_duration(self):
        m = CommunityMetrics()
        with m.time_block("agentcompute_slice_duration_seconds", labels={"tenant": "t1"}):
            time.sleep(0.01)
        cell = m.snapshot()["observe"][("agentcompute_slice_duration_seconds", (("tenant", "t1"),))]
        assert cell["count"] == 1
        assert cell["sum"] >= 0.01

    def test_time_block_records_even_when_body_raises(self):
        m = CommunityMetrics()
        with pytest.raises(RuntimeError):
            with m.time_block("block"):
                raise RuntimeError("body blew up")
        assert m.snapshot()["observe"][("block", ())]["count"] == 1

    def test_unhashable_label_value_is_str_coerced_never_raises(self):
        m = CommunityMetrics()
        m.increment("names", labels={"payload": ["a", "list"]})
        m.increment("names", labels={"payload": ["a", "list"]})
        m.set_gauge("g", 1.0, labels={"v": {"dict": True}})
        m.observe("o", 3.0, labels={"v": {"dict": True}})
        snap = m.snapshot()
        assert snap["increment"][("names", (("payload", "['a', 'list']"),))] == 2.0
        assert snap["set_gauge"][("g", (("v", "{'dict': True}"),))] == 1.0
        assert snap["observe"][("o", (("v", "{'dict': True}"),))]["sum"] == 3.0

    def test_snapshot_is_a_copy_not_a_live_view(self):
        m = CommunityMetrics()
        m.increment("c")
        snap = m.snapshot()
        m.increment("c")
        assert snap["increment"][("c", ())] == 1.0
        assert m.snapshot()["increment"][("c", ())] == 2.0


# ── NoopMetrics ───────────────────────────────────────────────────────────


class TestNoopMetrics:
    def test_all_operations_silent(self):
        m = NoopMetrics()
        m.increment("x", labels={"k": "v"})
        m.observe("x", 1.0, labels={"k": "v"})
        m.set_gauge("x", 1.0, labels={"k": "v"})
        assert m.calls_to("increment") and m.calls_to("observe")
        assert m.calls_to("set_gauge")

    def test_time_block_usable_as_context_manager(self):
        m = NoopMetrics()
        with m.time_block("x"):
            pass

    def test_time_block_usable_when_body_raises(self):
        m = NoopMetrics()
        with pytest.raises(RuntimeError):
            with m.time_block("x"):
                raise RuntimeError("propagates")

    def test_records_calls_for_test_seam(self):
        m = NoopMetrics()
        m.increment("named-counter", labels={"a": 1})
        (call,) = m.calls_to("increment")
        assert call.args == ("named-counter",)


# ── Facade routing ───────────────────────────────────────────────────────


def _mk_run(goal="obs goal") -> dm.Run:
    return dm.Run(
        run_id=dm.RunId(uuid.uuid4().hex),
        tenant=TENANT,
        goal=goal,
        agent_catalog=(dm.AgentSpecRaw(name="a1", role="research"),),
        provider=dm.ProviderId("stub"),
        request_metadata=dm.RequestMetadata(),
        status=dm.RunStatus.RUNNING,
        started_at=dm.EpochMillis(int(time.time() * 1000)),
    )


class TestFacadeRouting:
    def _obs(self) -> tuple[AgentComputeObservability, RecordingMetrics]:
        rec = RecordingMetrics()
        return AgentComputeObservability(rec), rec

    def test_slice_duration_routes_time_block_with_tenant(self):
        obs, rec = self._obs()
        with obs.slice_duration(_mk_run()):
            pass
        assert rec.events == [
            ("time_block", "agentcompute_slice_duration_seconds",
             None, {"tenant": TENANT}),
        ]

    def test_run_claim_and_agent_spawn(self):
        obs, rec = self._obs()
        obs.run_claim("claim")
        obs.run_claim("reclaim")
        obs.agent_spawn(dm.AgentKind.LLM)
        obs.agent_spawn(dm.AgentKind.AVENET)
        assert rec.increments_of("agentcompute_run_claim_total") == [
            {"kind": "claim"}, {"kind": "reclaim"},
        ]
        assert rec.increments_of("agentcompute_agent_spawn_total") == [
            {"kind": "llm"}, {"kind": "avernet"},
        ]

    @pytest.mark.parametrize("outcome", ["ok", "transient", "non_retryable", "exhausted", "timeout"])
    def test_llm_call_outcome_classes(self, outcome):
        obs, rec = self._obs()
        obs.llm_call(outcome)
        assert rec.increments_of("agentcompute_llm_call_total") == [{"outcome": outcome}]

    def test_token_usage_observes_total_with_tenant_and_kind(self):
        obs, rec = self._obs()
        obs.token_usage(
            dm.TenantId("t-9"), dm.LlmCallKind.ORCHESTRATOR,
            dm.TokenUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7),
        )
        obs.token_usage(
            UNATTRIBUTED_TENANT, dm.LlmCallKind.SUBAGENT,
            dm.TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )
        assert rec.of_op("observe") == [
            ("observe", "agentcompute_llm_tokens_total", 7.0,
             {"tenant": "t-9", "kind": "orchestrator"}),
            ("observe", "agentcompute_llm_tokens_total", 2.0,
             {"tenant": UNATTRIBUTED_TENANT, "kind": "subagent"}),
        ]

    def test_shard_persisted_normalizes_artifact_bool_lower(self):
        obs, rec = self._obs()
        obs.shard_persisted(False)
        obs.shard_persisted(True)
        assert rec.increments_of("agentcompute_shard_persisted_total") == [
            {"artifact": "false"}, {"artifact": "true"},
        ]

    def test_agent_terminal_and_stuck_and_queue_depth(self):
        obs, rec = self._obs()
        obs.agent_terminal(dm.AgentStatus.COMPLETED)
        obs.agent_terminal(dm.AgentStatus.FAILED)
        obs.stuck_transition()
        obs.queue_depth(11)
        assert rec.increments_of("agentcompute_agent_terminal_total") == [
            {"status": "COMPLETED"}, {"status": "FAILED"},
        ]
        assert rec.increments_of("agentcompute_stuck_transition_total") == [{}]
        assert rec.events[-1] == ("set_gauge", "agentcompute_queue_depth", 11.0, {})

    def test_null_observability_never_raises_and_records_nothing(self):
        NULL_OBSERVABILITY.run_claim("claim")
        NULL_OBSERVABILITY.agent_spawn(dm.AgentKind.LLM)
        NULL_OBSERVABILITY.llm_call("ok")
        NULL_OBSERVABILITY.token_usage(
            dm.TenantId("t"), dm.LlmCallKind.SUBAGENT, dm.TokenUsage(),
        )
        NULL_OBSERVABILITY.shard_persisted(True)
        NULL_OBSERVABILITY.agent_terminal(dm.AgentStatus.KILLED)
        NULL_OBSERVABILITY.stuck_transition()
        NULL_OBSERVABILITY.queue_depth(0)
        with NULL_OBSERVABILITY.slice_duration(_mk_run()):
            pass
        with pytest.raises(RuntimeError):
            with NULL_OBSERVABILITY.slice_duration(_mk_run()):
                raise RuntimeError("still propagates")


# ── OpenAICompatibleClient hooks ─────────────────────────────────────────


def _ok_body(tokens=(2, 3, 5)) -> dict:
    return {
        "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": tokens[0],
            "completion_tokens": tokens[1],
            "total_tokens": tokens[2],
        },
    }


class FakeOpenAIServer:
    """Sequenced transport mirror of test_llm_client.FakeOpenAIServer."""

    def __init__(self, responses=None) -> None:
        self.responses = list(responses or [])

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.responses:
            item = self.responses.pop(0)
        else:
            item = httpx.Response(200, json=_ok_body())
        if isinstance(item, Exception):
            raise item
        return item


def _client(server: FakeOpenAIServer, obs, **kwargs) -> OpenAICompatibleClient:
    kwargs.setdefault("base_url", "http://fake-llm.local/v1")
    kwargs.setdefault("api_key", "test-api-key")
    kwargs.setdefault("default_model", "default-model-x")
    client = OpenAICompatibleClient(observability=obs, **kwargs)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(server),
        base_url=client._base_url,
        headers={"Authorization": f"Bearer {kwargs['api_key']}"},
        timeout=client._timeout,
    )
    return client


def _req(kind=dm.LlmCallKind.SUBAGENT, user_prompt="do it") -> dm.CompletionRequest:
    return dm.CompletionRequest(
        kind=kind, model=None, system_prompt="", user_prompt=user_prompt,
        max_tokens=None, temperature=0.0,
    )


async def _instant_sleep(seconds: float) -> None:
    return None


def _skip_retry_waits(monkeypatch) -> None:
    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)


class TestLLMClientHooks:
    def test_success_emits_ok_and_token_usage(self):
        rec = RecordingMetrics()
        obs = AgentComputeObservability(rec)

        async def main():
            server = FakeOpenAIServer([httpx.Response(200, json=_ok_body())])
            client = _client(server, obs)
            resp = await client.complete(_req(kind=dm.LlmCallKind.ORCHESTRATOR))
            await client._client.aclose()
            assert resp.token_usage == dm.TokenUsage(2, 3, 5)

        asyncio.run(main())
        assert rec.increments_of("agentcompute_llm_call_total") == [{"outcome": "ok"}]
        assert rec.of_op("observe") == [
            ("observe", "agentcompute_llm_tokens_total", 5.0,
             {"tenant": UNATTRIBUTED_TENANT, "kind": "orchestrator"}),
        ]

    def test_transient_then_success_counts_both_classes(self, monkeypatch):
        _skip_retry_waits(monkeypatch)
        rec = RecordingMetrics()
        obs = AgentComputeObservability(rec)

        async def main():
            server = FakeOpenAIServer([
                httpx.Response(503, json={"error": "transient"}),
                httpx.Response(200, json=_ok_body()),
            ])
            client = _client(server, obs, max_retries=3)
            await client.complete(_req())
            await client._client.aclose()

        asyncio.run(main())
        assert rec.increments_of("agentcompute_llm_call_total") == [
            {"outcome": "transient"}, {"outcome": "ok"},
        ]
        assert len(rec.of_op("observe")) == 1

    def test_timeout_then_success_counts_timeout_class(self, monkeypatch):
        _skip_retry_waits(monkeypatch)
        rec = RecordingMetrics()
        obs = AgentComputeObservability(rec)

        async def main():
            server = FakeOpenAIServer([
                httpx.ReadTimeout("stalled"),
                httpx.Response(200, json=_ok_body()),
            ])
            client = _client(server, obs, max_retries=3)
            await client.complete(_req())
            await client._client.aclose()

        asyncio.run(main())
        assert rec.increments_of("agentcompute_llm_call_total") == [
            {"outcome": "timeout"}, {"outcome": "ok"},
        ]

    def test_exhaustion_counts_each_attempt_then_exhausted(self, monkeypatch):
        _skip_retry_waits(monkeypatch)
        rec = RecordingMetrics()
        obs = AgentComputeObservability(rec)

        async def main():
            server = FakeOpenAIServer([httpx.Response(500, json={}) for _ in range(3)])
            client = _client(server, obs, max_retries=2)
            with pytest.raises(RuntimeError, match="failed after 3 attempts"):
                await client.complete(_req())
            await client._client.aclose()

        asyncio.run(main())
        assert rec.increments_of("agentcompute_llm_call_total") == [
            {"outcome": "transient"}, {"outcome": "transient"}, {"outcome": "exhausted"},
        ]
        assert rec.of_op("observe") == []

    def test_client_error_counts_non_retryable_once(self):
        rec = RecordingMetrics()
        obs = AgentComputeObservability(rec)

        async def main():
            server = FakeOpenAIServer([httpx.Response(422, json={"error": "bad"})])
            client = _client(server, obs, max_retries=3)
            with pytest.raises(RuntimeError, match=r"failed \(client error\): 422"):
                await client.complete(_req())
            await client._client.aclose()

        asyncio.run(main())
        assert rec.increments_of("agentcompute_llm_call_total") == [
            {"outcome": "non_retryable"},
        ]

    def test_no_observability_configured_changes_nothing(self, monkeypatch):
        _skip_retry_waits(monkeypatch)

        async def main():
            server = FakeOpenAIServer([
                httpx.Response(500, json={}),
                httpx.Response(200, json=_ok_body()),
            ])
            client = _client(server, None, max_retries=1)
            resp = await client.complete(_req())
            await client._client.aclose()
            assert resp.text == "Hello"

        asyncio.run(main())

        default_built = OpenAICompatibleClient(
            base_url="http://fake-llm.local/v1", api_key="k",
            default_model="m",
        )
        assert default_built._obs is None
        asyncio.run(default_built._client.aclose())


# ── SliceStore hooks (real SQLite) ────────────────────────────────────────


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
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
    yield _TestDB(engine)
    engine.dispose()


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


def _completion(run: dm.Run, agent_id: dm.AgentId, status: dm.AgentStatus,
                result=None, artifact_key=None, error=None) -> dict:
    return {
        "run_id": run.run_id, "tenant": run.tenant,
        "agent_id": agent_id, "status": status, "result": result,
        "artifact_key": artifact_key, "error": error,
        "attempts": 1, "token_usage": dm.TokenUsage(1, 1, 2),
    }


class TestSliceStoreHooks:
    def _store(self, db, obs, lost_probe=lambda: False) -> SliceStore:
        return SliceStore(
            AgentComputeConfig(), AgentRepository(db), MessageRepository(db),
            ResultShardRepository(db), RoundRepository(db), lost_probe,
            observability=obs,
        )

    def test_completed_persistence_emits_terminal_and_shard(self, db):
        run = _mk_run()
        RunRepository(db).create(run)
        agent = _mk_agent(run, "a1")
        AgentRepository(db).create(agent)
        rec = RecordingMetrics()
        store = self._store(db, AgentComputeObservability(rec))

        store.persist_completion(
            _completion(run, agent.agent_id, dm.AgentStatus.COMPLETED,
                        result={"r": 1}),
            claim_epoch=0,
        )

        assert rec.increments_of("agentcompute_agent_terminal_total") == [
            {"status": "COMPLETED"},
        ]
        assert rec.increments_of("agentcompute_shard_persisted_total") == [
            {"artifact": "false"},
        ]
        shard = ResultShardRepository(db).get_by_run_agent(
            run.run_id, agent.agent_id, TENANT,
        )
        assert shard is not None

    def test_artifact_shard_labels_artifact_true(self, db):
        run = _mk_run()
        RunRepository(db).create(run)
        agent = _mk_agent(run, "a1")
        AgentRepository(db).create(agent)
        rec = RecordingMetrics()
        store = self._store(db, AgentComputeObservability(rec))

        store.persist_completion(
            _completion(run, agent.agent_id, dm.AgentStatus.COMPLETED,
                        artifact_key="run/artifact"),
            claim_epoch=0,
        )

        assert rec.increments_of("agentcompute_shard_persisted_total") == [
            {"artifact": "true"},
        ]

    def test_failure_persistence_emits_terminal_without_shard(self, db):
        run = _mk_run()
        RunRepository(db).create(run)
        agent = _mk_agent(run, "a1")
        AgentRepository(db).create(agent)
        rec = RecordingMetrics()
        store = self._store(db, AgentComputeObservability(rec))

        store.persist_completion(
            _completion(run, agent.agent_id, dm.AgentStatus.FAILED, error="boom"),
            claim_epoch=0,
        )

        assert rec.increments_of("agentcompute_agent_terminal_total") == [
            {"status": "FAILED"},
        ]
        assert rec.increments_of("agentcompute_shard_persisted_total") == []

    def test_malformed_xor_shard_refused_not_counted(self, db):
        run = _mk_run()
        RunRepository(db).create(run)
        agent = _mk_agent(run, "a1")
        AgentRepository(db).create(agent)
        rec = RecordingMetrics()
        store = self._store(db, AgentComputeObservability(rec))

        store.persist_completion(
            _completion(run, agent.agent_id, dm.AgentStatus.COMPLETED),
            claim_epoch=0,
        )

        assert rec.increments_of("agentcompute_agent_terminal_total") == [
            {"status": "COMPLETED"},
        ]
        assert rec.increments_of("agentcompute_shard_persisted_total") == []

    def test_lost_probe_emits_nothing(self, db):
        run = _mk_run()
        RunRepository(db).create(run)
        agent = _mk_agent(run, "a1")
        AgentRepository(db).create(agent)
        rec = RecordingMetrics()
        store = self._store(db, AgentComputeObservability(rec), lost_probe=lambda: True)

        store.persist_completion(
            _completion(run, agent.agent_id, dm.AgentStatus.COMPLETED, result={}),
            claim_epoch=0,
        )

        assert rec.events == []

    def test_default_observability_is_null_and_silent(self, db):
        run = _mk_run()
        RunRepository(db).create(run)
        agent = _mk_agent(run, "a1")
        AgentRepository(db).create(agent)
        store = self._store(db, NULL_OBSERVABILITY)

        store.persist_completion(
            _completion(run, agent.agent_id, dm.AgentStatus.COMPLETED, result={"ok": 1}),
            claim_epoch=0,
        )
        shard = ResultShardRepository(db).get_by_run_agent(
            run.run_id, agent.agent_id, TENANT,
        )
        assert shard is not None


# ── SwarmRunHandler hooks ────────────────────────────────────────────────


class _ObsRecordingSubagent:
    """Immediate-result fake subagent (test_swarm_slice.FakeSubagent shape)."""

    def __init__(self, name: str, output) -> None:
        self.name = name
        self._output = output

    async def setup(self) -> None:
        return None

    async def teardown(self) -> None:
        return None

    async def halt(self) -> None:
        return None

    async def run_task(self, envelope: dm.TaskEnvelope) -> dm.SubagentResult:
        return dm.SubagentResult(output=self._output)


class _ObsRecordingFactory:
    def create(self, spec: dm.AgentSpecRaw, run: dm.Run):
        return _ObsRecordingSubagent(spec.name, {"did": spec.name})


def _obs_config() -> AgentComputeConfig:
    return AgentComputeConfig(
        orchestration=AgentComputeOrchestrationConfig(
            slice_max_seconds=8.0, slice_interval=0.0,
            orchestrator_turn_interval=0.01, subagent_max_attempts=3,
            max_width=8,
        ),
        message_retention_tail=50,
    )


def _obs_handler(db, obs, script) -> SwarmRunHandler:
    cfg = _obs_config()
    orchestrator = Orchestrator(
        llm=StubLLMProvider(script=script),
        parser=DecisionParser(),
        guards=GuardService(max_width=8, max_depth=5),
        config=cfg,
    )
    runtime = SubagentRuntime(
        factory=_ObsRecordingFactory(),
        artifact_store=None,
        max_width=8,
        max_attempts=3,
        per_task_timeout=cfg.orchestration.per_subagent_timeout,
        artifact_inline_threshold=16384,
    )
    return SwarmRunHandler(
        config=cfg,
        orchestrator=orchestrator,
        runtime=runtime,
        run_repo=RunRepository(db),
        agent_repo=AgentRepository(db),
        message_repo=MessageRepository(db),
        shard_repo=ResultShardRepository(db),
        round_repo=RoundRepository(db),
        guard_repo=GuardEventRepository(db),
        job_repo=JobRepository(db),
        termination=TerminationEvaluator(),
        guards=GuardService(),
        observability=obs,
    )


def _enqueue_and_claim(db, run: dm.Run) -> int:
    JobRepository(db).enqueue(run_id=run.run_id, tenant=TENANT,
                              deadline_seconds=3600)
    return JobRepository(db).claim_batch(WORKER, [TENANT], 1, 60)[0]["claim_epoch"]


class TestSwarmRunHooks:
    def test_slice_and_spawn_emitted_on_durable_run(self, db):
        import json as _json
        script = [
            _json.dumps({"kind": "spawn_agent",
                         "agents": [{"name": "a1", "objective": "goal-a1"}]}),
            _json.dumps({"kind": "wait", "refs": ["a1"], "mode": "all"}),
            _json.dumps({"kind": "finish", "answer": "done"}),
        ]
        rec = RecordingMetrics()
        handler = _obs_handler(db, AgentComputeObservability(rec), script)
        run = _mk_run()
        RunRepository(db).create(run)
        claim_epoch = _enqueue_and_claim(db, run)

        outcome = asyncio.run(handler.handle_slice(
            run.run_id, TENANT, claim_epoch=claim_epoch, worker_id=WORKER,
        ))

        assert outcome == "complete"
        assert len(rec.of_op("time_block")) == 1
        assert rec.of_op("time_block")[0][1] == "agentcompute_slice_duration_seconds"
        assert rec.increments_of("agentcompute_agent_spawn_total") == [
            {"kind": "llm"},
        ]
        assert {"status": "COMPLETED"} in rec.increments_of(
            "agentcompute_agent_terminal_total",
        )
        assert rec.increments_of("agentcompute_shard_persisted_total") == [
            {"artifact": "false"},
        ]

    def test_cancelled_slice_short_circuits_before_measurement(self, db):
        # The cancel branch performs no executed slice work, so no duration
        # sample exists — timing starts only at the decision loop below.
        rec = RecordingMetrics()
        handler = _obs_handler(db, AgentComputeObservability(rec), [])
        run = _mk_run()
        RunRepository(db).create(run)
        claim_epoch = _enqueue_and_claim(db, run)

        outcome = asyncio.run(handler.handle_slice(
            run.run_id, TENANT, claim_epoch=claim_epoch, cancel_requested=True,
            worker_id=WORKER,
        ))

        assert outcome == "complete"
        assert rec.of_op("time_block") == []

    def test_force_stuck_on_running_run_emits_stuck_transition(self, db):
        rec = RecordingMetrics()
        handler = _obs_handler(db, AgentComputeObservability(rec), [])
        run = _mk_run()
        RunRepository(db).create(run)
        claim_epoch = _enqueue_and_claim(db, run)

        ok = handler.force_stuck(run.run_id, TENANT, claim_epoch=claim_epoch,
                                 error="breaker tripped", worker_id=WORKER)

        assert ok is True
        assert rec.increments_of("agentcompute_stuck_transition_total") == [{}]

    def test_force_stuck_on_terminal_run_emits_nothing(self, db):
        rec = RecordingMetrics()
        handler = _obs_handler(db, AgentComputeObservability(rec), [])
        run = dm.Run(
            run_id=dm.RunId(uuid.uuid4().hex), tenant=TENANT, goal="g",
            agent_catalog=(), provider=dm.ProviderId("stub"),
            request_metadata=dm.RequestMetadata(),
            status=dm.RunStatus.COMPLETED,
            started_at=dm.EpochMillis(0),
        )
        RunRepository(db).create(run)
        JobRepository(db).enqueue(run_id=run.run_id, tenant=TENANT,
                                  deadline_seconds=3600)

        ok = handler.force_stuck(run.run_id, TENANT, claim_epoch=1, error="late")

        assert ok is False
        assert rec.events == []


# ── AvernetSubagent hooks ────────────────────────────────────────────────

CREATE_PATH = "/openapi/v1/bots/with-manifest"
STATUS_PATH_TEMPLATE = "/openapi/v1/bots/{bot_id}/status"
CHAT_PATH = "/openapi/v1/chat/stream"


def _mk_envelope() -> dm.TaskEnvelope:
    return dm.TaskEnvelope(
        name="av1", objective="o", output_schema={"type": "object"},
        boundaries="", parent_goal="g",
    )


class TestAvernetSubagentHooks:
    """run_task outcome counters against a MockTransport backend gateway
    (paths mirror test_avernet_subagent.FakeAvernetGateway)."""

    @staticmethod
    def _make_sub(obs) -> AvernetSubagent:
        cfg = AgentComputeAvernetConfig(backend_base_url="http://backend.local")
        sub = AvernetSubagent(
            spec=dm.AgentSpecRaw(name="av1", role="research"),
            run=_mk_run(), llm=None, config=cfg, observability=obs,
        )
        return sub

    @staticmethod
    def _run(sub: AvernetSubagent) -> None:
        asyncio.run(sub.setup())

    def _drive(self, obs, chat_outcome) -> list[dict]:
        rec = RecordingMetrics()
        obs = obs or AgentComputeObservability(rec)

        async def gateway(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == CREATE_PATH:
                return httpx.Response(200, json={"data": {"bot_id": "bot-1"}})
            if path == STATUS_PATH_TEMPLATE.format(bot_id="bot-1"):
                return httpx.Response(200, json={"data": {"state": "READY"}})
            if path == CHAT_PATH:
                if isinstance(chat_outcome, Exception):
                    raise chat_outcome
                if chat_outcome == "ok":
                    return httpx.Response(200, text="plain-answer")
                return httpx.Response(chat_outcome, text="chat exploded")
            return httpx.Response(404, json={"error": "no such endpoint"})

        async def main():
            sub = self._make_sub(obs)
            sub._client = httpx.AsyncClient(
                transport=httpx.MockTransport(gateway),
                base_url="http://backend.local",
            )
            try:
                await sub.setup()
                await sub.run_task(_mk_envelope())
            except Exception:
                pass
            finally:
                await sub._client.aclose()

        asyncio.run(main())
        return rec.increments_of("agentcompute_llm_call_total")

    def test_success_emits_ok(self):
        outcomes = self._drive(None, "ok")
        assert outcomes == [{"outcome": "ok"}]

    def test_http_status_error_emits_non_retryable(self):
        outcomes = self._drive(None, 410)
        assert outcomes == [{"outcome": "non_retryable"}]

    def test_transport_error_emits_exhausted(self):
        outcomes = self._drive(None, httpx.ConnectError("backend down"))
        assert outcomes == [{"outcome": "exhausted"}]

    def test_without_observability_behaves_as_before(self):
        async def gateway(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == CREATE_PATH:
                return httpx.Response(200, json={"data": {"bot_id": "bot-1"}})
            if path == STATUS_PATH_TEMPLATE.format(bot_id="bot-1"):
                return httpx.Response(200, json={"data": {"state": "READY"}})
            return httpx.Response(200, text="payload")

        async def main():
            sub = self._make_sub(None)
            sub._client = httpx.AsyncClient(
                transport=httpx.MockTransport(gateway),
                base_url="http://backend.local",
            )
            await sub.setup()
            result = await sub.run_task(_mk_envelope())
            await sub._client.aclose()
            return result

        result = asyncio.run(main())
        assert result.output == {"text": "payload"}


# ── DI wiring ─────────────────────────────────────────────────────────────


class TestDIWiring:
    def test_column_metrics_binding_overrides_agentcompute_fallback(self):
        injector = Injector([AgentComputeModule(), CommunityMetricsModule()])
        metrics = injector.get(MetricsPlugin)
        assert isinstance(metrics, CommunityMetrics)
        obs = injector.get(AgentComputeObservability)
        assert isinstance(obs._metrics, CommunityMetrics)

    def test_fallback_binding_is_noop_without_a_column_module(self):
        injector = Injector([AgentComputeModule()])
        metrics = injector.get(MetricsPlugin)
        assert isinstance(metrics, NoopMetrics)
        obs = injector.get(AgentComputeObservability)
        assert isinstance(obs._metrics, NoopMetrics)

    def test_observability_reaches_llm_client_and_handler(self):
        # Repos hold the DatabasePlugin without using it at construction
        # time, so wiring inspection needs only a placeholder instance.
        class _StubDatabase:
            def orm_session(self):
                raise AssertionError("not exercised by this test")

        class _LlmReadyModule(Module):
            def configure(self, binder):
                binder.bind(DatabasePlugin, to=_StubDatabase())

            @provider
            @singleton
            def provide_config(self) -> AgentComputeConfig:
                return AgentComputeConfig(llm=LlmConfig(
                    base_url="http://llm.local/v1", api_key="k",
                    default_model="m",
                ))

        injector = Injector([
            AgentComputeModule(),
            CommunityMetricsModule(),
            _LlmReadyModule(),
        ])
        llm = injector.get(LLMProvider)
        assert isinstance(llm, OpenAICompatibleClient)
        assert isinstance(llm._obs._metrics, CommunityMetrics)
        handler = injector.get(SwarmRunHandler)
        assert isinstance(handler._obs._metrics, CommunityMetrics)
        assert isinstance(handler.store._obs._metrics, CommunityMetrics)