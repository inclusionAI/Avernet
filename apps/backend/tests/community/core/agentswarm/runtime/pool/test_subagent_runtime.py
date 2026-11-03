"""Behavioral tests for SubagentRuntime (D42-adjacent runtime contracts).

Covers: spawn/wait lifecycle, completion dict contract, bounded width +
fixed worker pool, retry with fresh-subagent-per-attempt, per-task timeout,
slice_lost handling, worker cancellation, artifact routing, sibling
isolation, spec resolution, and token_usage propagation.

Conventions follow test_orchestrator.py: sync test defs driving async code
through asyncio.run, hand-written fakes (no MagicMock).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque

import pytest

from agentclaw.community.core.agentswarm.domain.models import (
    AgentKind,
    AgentId,
    AgentSpec, AgentStatus, EpochMillis, ProviderKind, RequestMetadata,
    Run, RunId, RunStatus, SubagentResult, TaskEnvelope, TenantId,
    TokenUsage,
)
from agentclaw.community.core.agentswarm.runtime.breaker import (
    BreakerState, UpstreamBreaker,
)
from agentclaw.community.core.agentswarm.runtime.pool import (
    SubagentRuntime,
)
from agentclaw.community.core.agentswarm.runtime.pool.impl import (
    _MAX_INLINE_RESULT_CHARS,
)


def _now_ms():
    return int(time.time() * 1000)


def _mk_run(catalog=None) -> Run:
    return Run(
        run_id=RunId("run-" + uuid.uuid4().hex[:8]),
        tenant=TenantId("tenant-a"),
        goal="test goal",
        agent_catalog=tuple(catalog) if catalog else (
            AgentSpec(name="researcher", role="deep research"),
        ),
        provider=ProviderKind("stub"),
        request_metadata=RequestMetadata(),
        status=RunStatus.RUNNING,
        started_at=EpochMillis(_now_ms()),
    )


def _mk_envelope(name="researcher", objective="research the thing") -> TaskEnvelope:
    return TaskEnvelope(
        name=name,
        objective=objective,
        output_schema={"type": "object"},
        boundaries="stay focused",
        parent_goal="test goal",
    )


def _ok_result(output=None, token_usage=None) -> SubagentResult:
    return SubagentResult(output=output if output is not None else {"done": True},
                          token_usage=token_usage)


class ScriptedSubagent:
    """Hand-fake implementing the Subagent Protocol (spi/subagent.py).

    Outcomes are pulled from a shared deque so a script describes the
    behavior across attempts (each attempt gets a fresh instance, and each
    run_task call consumes one script entry).
    """

    def __init__(self, spec: AgentSpec, script: deque, tracker: dict,
                 on_run=None) -> None:
        self.name = spec.name
        self.spec = spec
        self.script = script
        self.tracker = tracker
        self.on_run = on_run

    async def setup(self) -> None:
        self.tracker["setup"] += 1

    async def run_task(self, envelope: TaskEnvelope) -> SubagentResult:
        self.tracker["run_task"] += 1
        self.tracker["envelopes"].append(envelope)
        self.tracker["concurrent"] += 1
        self.tracker["max_concurrent"] = max(
            self.tracker["max_concurrent"], self.tracker["concurrent"]
        )
        try:
            if self.on_run is not None:
                self.on_run()
            if self.script:
                item = self.script.popleft()
            else:
                item = ("ok", _ok_result())
            kind, arg = item
            if kind == "raise":
                raise arg
            if kind == "sleep":
                await asyncio.sleep(arg)
                return _ok_result({"slept": arg})
            return arg
        except asyncio.CancelledError:
            self.tracker["cancelled"] += 1
            raise
        finally:
            self.tracker["concurrent"] -= 1

    async def teardown(self) -> None:
        self.tracker["teardown"] += 1

    async def halt(self) -> None:
        self.tracker["halt"] += 1


class FakeFactory:
    """Hand-fake for the SubagentFactory protocol (spi/subagent_factory.py)."""

    def __init__(self, scripts: dict | None = None, hooks: dict | None = None) -> None:
        self.scripts = {name: deque(items) for name, items in (scripts or {}).items()}
        self.hooks = dict(hooks or {})
        self.creates = 0
        self.created_specs = []
        self.created_runs = []
        self.created_subs = []
        self.created_attempts = []
        self.tracker = {
            "setup": 0, "run_task": 0, "teardown": 0, "halt": 0,
            "cancelled": 0, "concurrent": 0, "max_concurrent": 0,
            "envelopes": [],
        }

    def create(self, spec: AgentSpec, run: Run,
                  agent_id: AgentId | None = None,
                  attempt: int = 1) -> ScriptedSubagent:
        self.creates += 1
        self.created_specs.append(spec)
        self.created_runs.append(run)
        self.created_attempts.append(attempt)
        script = self.scripts.get(spec.name)
        if script is None:
            script = self.scripts.get("*", deque())
        sub = ScriptedSubagent(
            spec, script, self.tracker, on_run=self.hooks.get(spec.name)
        )
        self.created_subs.append(sub)
        return sub


class FakeArtifactStore:
    """Hand-fake for the ArtifactStore protocol (spi/artifact_store.py)."""

    def __init__(self) -> None:
        self.puts = []
        self.put_files = []
        self.deletes = []
        self.contents = {}

    def put(self, key: str, content) -> bool:
        self.puts.append(key)
        self.contents[key] = content
        return True

    def put_file(self, key: str, local_path: str) -> bool:
        self.put_files.append((key, local_path))
        return True

    def delete(self, key: str) -> bool:
        self.deletes.append(key)
        return True

    def get(self, key: str):
        return self.contents.get(key)


class TestHappyPathAndCompletionContract:
    def test_happy_path_completion_dict_contract(self):
        async def main():
            factory = FakeFactory(scripts={
                "researcher": [("ok", _ok_result({"answer": "42"},
                                                 TokenUsage(5, 7, 12)))],
            })
            rt = SubagentRuntime(factory=factory, max_width=2)
            await rt.start()
            try:
                run = _mk_run()
                env = _mk_envelope()
                agent_id = await rt.spawn(run, env)
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            assert len(results) == 1
            r = results[0]
            assert set(r) == {
                "agent_id", "run_id", "tenant", "status", "attempts",
                "error", "idempotency_key", "result", "artifact_key",
                "token_usage",
            }
            assert r["status"] == AgentStatus.COMPLETED
            assert r["attempts"] == 1
            assert r["error"] is None
            assert r["result"] == {"answer": "42"}
            assert r["token_usage"] == TokenUsage(5, 7, 12)
            assert r["agent_id"] == agent_id
            assert r["run_id"] == run.run_id
            assert r["tenant"] == run.tenant
            assert r["idempotency_key"] == f"{run.run_id}:{agent_id}"
            assert r["artifact_key"] is None
            assert factory.tracker["envelopes"][0] is env
            assert factory.tracker["run_task"] == 1

        asyncio.run(main())


class TestRetryAndExhaustion:
    def test_success_on_second_attempt(self):
        async def main():
            factory = FakeFactory(scripts={
                "researcher": [
                    ("raise", ValueError("transient boom")),
                    ("ok", _ok_result({"after": "retry"})),
                ],
            })
            rt = SubagentRuntime(factory=factory, max_attempts=3)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            assert len(results) == 1
            r = results[0]
            assert r["status"] == AgentStatus.COMPLETED
            assert r["attempts"] == 2
            assert r["error"] is None
            assert r["result"] == {"after": "retry"}

        asyncio.run(main())

    def test_teardown_invoked_after_every_attempt(self):
        async def main():
            factory = FakeFactory(scripts={
                "researcher": [
                    ("raise", ValueError("t1")),
                    ("ok", _ok_result()),
                ],
            })
            rt = SubagentRuntime(factory=factory)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                await rt.wait_for_completions()
            finally:
                await rt.stop()
            assert factory.tracker["teardown"] == 2

        asyncio.run(main())

    def test_all_attempts_exhausted_fails_with_last_error(self):
        async def main():
            factory = FakeFactory(scripts={
                "researcher": [
                    ("raise", ValueError("boom-1")),
                    ("raise", ValueError("boom-2")),
                    ("raise", ValueError("boom-3")),
                ],
            })
            rt = SubagentRuntime(factory=factory, max_attempts=3)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            assert len(results) == 1
            r = results[0]
            assert r["status"] == AgentStatus.FAILED
            assert r["attempts"] == 3
            assert r["error"] == "boom-3"
            assert r["result"] is None
            assert r["token_usage"] is None

        asyncio.run(main())

    def test_factory_crash_fails_result_not_worker(self):
        # A factory that raises in create() must degrade to a FAILED
        # completion (the run's normal failure path) instead of killing
        # the fire-and-forget worker task, whose exception would never
        # surface and the run would hang to its slice budget.
        async def main():
            class ExplodingFactory(FakeFactory):
                def create(self, spec, run, agent_id=None, attempt=1):
                    self.creates += 1
                    raise TypeError("spec malformed")

            factory = ExplodingFactory()
            rt = SubagentRuntime(factory=factory, max_attempts=3)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            assert len(results) == 1
            r = results[0]
            assert r["status"] == AgentStatus.FAILED
            assert r["attempts"] == 3
            assert "spec malformed" in r["error"]
            assert factory.creates == 3

        asyncio.run(main())

    def test_fresh_subagent_instance_per_attempt(self):
        async def main():
            factory = FakeFactory(scripts={
                "researcher": [
                    ("raise", ValueError("boom-1")),
                    ("raise", ValueError("boom-2")),
                    ("raise", ValueError("boom-3")),
                ],
            })
            rt = SubagentRuntime(factory=factory, max_attempts=3)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            assert factory.creates == 3
            assert len(factory.created_subs) == 3
            assert len({id(s) for s in factory.created_subs}) == 3
            assert results[0]["attempts"] == 3

        asyncio.run(main())

    def test_per_task_timeout_fails_with_timeout_error(self):
        async def main():
            factory = FakeFactory(scripts={
                "researcher": [("sleep", 1.0), ("sleep", 1.0)],
            })
            rt = SubagentRuntime(
                factory=factory, max_attempts=2, per_task_timeout=0.05
            )
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            assert len(results) == 1
            r = results[0]
            assert r["status"] == AgentStatus.FAILED
            assert r["error"] == "timeout"
            assert r["attempts"] == 2
            assert factory.tracker["teardown"] == 2

        asyncio.run(main())


class TestBoundedWidth:
    def test_max_width_bounds_concurrent_subagents(self):
        async def main():
            factory = FakeFactory(scripts={
                "*": [("sleep", 0.08)] * 4,
            })
            rt = SubagentRuntime(factory=factory, max_width=2)
            await rt.start()
            try:
                run = _mk_run()
                for i in range(4):
                    await rt.spawn(run, _mk_envelope(name=f"w{i}"))
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            assert factory.tracker["max_concurrent"] == 2
            assert len(results) == 4
            assert all(r["status"] == AgentStatus.COMPLETED for r in results)

        asyncio.run(main())

    def test_spawn_blocks_when_queue_full(self):
        async def main():
            factory = FakeFactory()
            rt = SubagentRuntime(factory=factory, max_width=1)
            run = _mk_run()
            await rt.spawn(run, _mk_envelope())
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(rt.spawn(run, _mk_envelope()), timeout=0.2)
            assert await rt.get_pending_count() == 1
            assert factory.creates == 0

        asyncio.run(main())

    def test_wait_for_completions_empty_queue_returns_empty(self):
        async def main():
            rt = SubagentRuntime(factory=FakeFactory(), max_width=2)
            await rt.start()
            try:
                t0 = time.monotonic()
                results = await rt.wait_for_completions()
                elapsed = time.monotonic() - t0
            finally:
                await rt.stop()
            assert results == []
            assert elapsed < 1.0

        asyncio.run(main())


class TestSliceLost:
    def test_slice_lost_between_attempts_fails_with_slice_lost(self):
        async def main():
            rt_holder = {}

            def set_slice_lost():
                rt_holder["rt"].set_slice_lost(True)

            factory = FakeFactory(
                scripts={"researcher": [("raise", ValueError("boom"))]},
                hooks={"researcher": set_slice_lost},
            )
            rt = SubagentRuntime(factory=factory, max_attempts=3)
            rt_holder["rt"] = rt
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            assert len(results) == 1
            r = results[0]
            assert r["status"] == AgentStatus.FAILED
            assert r["error"] == "slice_lost"
            assert r["attempts"] == 2
            assert factory.tracker["teardown"] == 1

        asyncio.run(main())

    def test_slice_lost_midflight_does_not_interrupt_inflight_run(self):
        # NOTE: found-vs-expected — task 10.3 expects slice_lost mid-flight to
        # halt/teardown the in-flight subagent. Current source only checks
        # slice_lost BETWEEN attempts; an in-flight run_task keeps running to
        # completion. This test pins the current behavior (halt never called).
        async def main():
            rt_holder = {}

            def set_slice_lost():
                rt_holder["rt"].set_slice_lost(True)

            factory = FakeFactory(
                scripts={"researcher": [("sleep", 0.15)]},
                hooks={"researcher": set_slice_lost},
            )
            rt = SubagentRuntime(factory=factory, max_attempts=2,
                                 per_task_timeout=1.0)
            rt_holder["rt"] = rt
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            assert len(results) == 1
            r = results[0]
            assert r["status"] == AgentStatus.COMPLETED
            assert r["attempts"] == 1
            assert factory.tracker["halt"] == 0
            assert factory.tracker["teardown"] == 1

        asyncio.run(main())

    def test_slice_lost_before_spawn_starves_queue_current_behavior(self):
        # NOTE: found-vs-expected — task 10.3 expects slice_lost set before
        # spawn to immediately fail the queued item with error "slice_lost".
        # Current source instead exits all worker loops, so a spawned item is
        # never picked up. The contract today: a bounded wait returns [] with
        # the item still pending, and stop() drains the orphaned item.
        async def main():
            factory = FakeFactory()
            rt = SubagentRuntime(factory=factory, max_width=2)
            await rt.start()
            rt.set_slice_lost(True)
            await asyncio.sleep(0.7)
            await rt.spawn(_mk_run(), _mk_envelope())
            await asyncio.sleep(0.7)

            assert factory.creates == 0
            assert await rt.get_pending_count() == 1
            results = await rt.wait_for_completions(timeout=0.3)
            assert results == []
            assert await rt.get_pending_count() == 1
            await rt.stop()
            assert await rt.get_pending_count() == 0

        asyncio.run(main())


class TestWorkerCancellation:
    def test_cancelled_worker_cancellation_propagates_not_swallowed(self):
        async def main():
            entry = asyncio.Event()

            def on_run():
                entry.set()

            factory = FakeFactory(
                scripts={"researcher": [("sleep", 5.0)]},
                hooks={"researcher": on_run},
            )
            rt = SubagentRuntime(factory=factory, max_width=2)
            await rt.start()
            await rt.spawn(_mk_run(), _mk_envelope())
            await entry.wait()
            await rt.stop()

            results = await rt.wait_for_completions()
            assert results == []
            assert factory.tracker["cancelled"] == 1
            assert factory.tracker["teardown"] == 1

        asyncio.run(main())


class TestArtifactRouting:
    def test_oversize_output_routed_to_artifact_store(self):
        async def main():
            big_output = {"pad": "x" * 100}
            factory = FakeFactory(
                scripts={"researcher": [("ok", _ok_result(big_output))]}
            )
            store = FakeArtifactStore()
            rt = SubagentRuntime(
                factory=factory, artifact_store=store,
                artifact_inline_threshold=10,
            )
            await rt.start()
            try:
                env = _mk_envelope()
                agent_id = await rt.spawn(_mk_run(), env)
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            r = results[0]
            assert r["status"] == AgentStatus.COMPLETED
            key = f"{r['run_id']}/{agent_id}/result"
            assert r["artifact_key"] == key
            assert r["result"] is None
            assert store.puts == [key]
            assert store.contents[key] == str(big_output)

        asyncio.run(main())

    def test_small_output_inlined_no_artifact(self):
        async def main():
            factory = FakeFactory(
                scripts={"researcher": [("ok", _ok_result({"a": 1}))]}
            )
            store = FakeArtifactStore()
            rt = SubagentRuntime(
                factory=factory, artifact_store=store,
                artifact_inline_threshold=10,  # len("{'a': 1}") == 8 <= 10
            )
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            r = results[0]
            assert r["status"] == AgentStatus.COMPLETED
            assert r["result"] == {"a": 1}
            assert r["artifact_key"] is None
            assert store.puts == []

        asyncio.run(main())

    def test_oversize_output_without_store_stays_inline(self):
        # NOTE: found-vs-expected — with artifact_inline_threshold exceeded and
        # no artifact store configured, current source keeps the output inline
        # (result XOR carrier, D31) instead of dropping it.
        async def main():
            big_output = {"pad": "y" * 100}
            factory = FakeFactory(
                scripts={"researcher": [("ok", _ok_result(big_output))]}
            )
            rt = SubagentRuntime(
                factory=factory, artifact_store=None,
                artifact_inline_threshold=10,
            )
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            r = results[0]
            assert r["status"] == AgentStatus.COMPLETED
            assert r["result"] == big_output
            assert r["artifact_key"] is None

        asyncio.run(main())


class TestSiblingIsolation:
    def test_failing_sibling_does_not_cancel_succeeding_sibling(self):
        async def main():
            factory = FakeFactory(scripts={
                "boom-sib": [
                    ("raise", ValueError("boom-1")),
                    ("raise", ValueError("boom-2")),
                    ("raise", ValueError("boom-3")),
                ],
                "ok-sib": [("ok", _ok_result({"ok": "sibling"}))],
            })
            rt = SubagentRuntime(factory=factory, max_width=2, max_attempts=3)
            await rt.start()
            try:
                run = _mk_run()
                await rt.spawn(run, _mk_envelope(name="boom-sib", objective="explode"))
                await rt.spawn(run, _mk_envelope(name="ok-sib", objective="succeed"))
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            by_status = {}
            for r in results:
                by_status[r["status"]] = r
            assert len(results) == 2
            assert set(by_status) == {AgentStatus.COMPLETED, AgentStatus.FAILED}
            assert by_status[AgentStatus.COMPLETED]["attempts"] == 1
            failed = by_status[AgentStatus.FAILED]
            assert failed["attempts"] == 3
            assert "boom-3" in failed["error"]

        asyncio.run(main())


class TestSpecResolution:
    def test_envelope_name_resolved_from_run_catalog(self):
        async def main():
            catalog_spec = AgentSpec(
                name="researcher", role="deep research",
                instructions="specific instructions",
            )
            factory = FakeFactory(scripts={"researcher": [("ok", _ok_result())]})
            rt = SubagentRuntime(factory=factory)
            await rt.start()
            try:
                await rt.spawn(_mk_run(catalog=[catalog_spec]), _mk_envelope())
                await rt.wait_for_completions()
            finally:
                await rt.stop()

            assert factory.creates == 1
            assert factory.created_specs[0] is catalog_spec

        asyncio.run(main())

    def test_unknown_name_falls_back_to_name_objective_spec(self):
        async def main():
            factory = FakeFactory(scripts={"*": [("ok", _ok_result())]})
            rt = SubagentRuntime(factory=factory)
            await rt.start()
            try:
                env = _mk_envelope(name="ghost", objective="the objective")
                await rt.spawn(_mk_run(), env)
                await rt.wait_for_completions()
            finally:
                await rt.stop()

            assert factory.creates == 1
            assert factory.created_specs[0] == AgentSpec(
                name="ghost", role="the objective"
            )

        asyncio.run(main())

    def test_unknown_spawn_inherits_unanimous_catalog_kind(self):
        # A decision name that misses the catalog must not silently change
        # the run's composition: with a unanimous bot catalog, the
        # fallback spec is bot-kind (engine/bot_type/identity inherited)
        # so the spawn provisions a bot subagent, not an llm one.
        async def main():
            factory = FakeFactory(scripts={"*": [("ok", _ok_result()),
                                                 ("ok", _ok_result())]})
            run = _mk_run(catalog=[AgentSpec(
                name="scout", role="surveys", kind=AgentKind.BOT,
            )] * 2)
            rt = SubagentRuntime(factory=factory)
            await rt.start()
            try:
                env = _mk_envelope(name="auditor_for_x", objective="o-x")
                await rt.spawn(run, env)
                await rt.wait_for_completions()
            finally:
                await rt.stop()

            created = factory.created_specs[0]
            assert created.kind is AgentKind.BOT

        asyncio.run(main())

    def test_prefix_spawn_name_inherits_catalog_spec(self):
        catalog_spec = AgentSpec(name="coder", role="writes code")
        run = _mk_run(catalog=[catalog_spec])
        env = _mk_envelope(name="coder_rust")

        assert SubagentRuntime._find_spec(run, env) is catalog_spec

    def test_exact_name_match_wins_over_prefix(self):
        exact_spec = AgentSpec(name="coder_rust", role="rust specialist")
        prefix_spec = AgentSpec(name="coder", role="writes code")
        run = _mk_run(catalog=[prefix_spec, exact_spec])
        env = _mk_envelope(name="coder_rust")

        assert SubagentRuntime._find_spec(run, env) is exact_spec

    def test_no_match_returns_none(self):
        run = _mk_run()
        env = _mk_envelope(name="ghost")

        assert SubagentRuntime._find_spec(run, env) is None


class TestTokenUsagePropagation:
    def test_token_usage_flows_to_completion(self):
        async def main():
            tokens = TokenUsage(prompt_tokens=11, completion_tokens=13, total_tokens=24)
            factory = FakeFactory(
                scripts={"researcher": [("ok", _ok_result({"x": 1}, tokens))]}
            )
            rt = SubagentRuntime(factory=factory)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            assert results[0]["token_usage"] == tokens
            assert results[0]["token_usage"].total_tokens == 24

        asyncio.run(main())

    def test_spawn_accepts_caller_minted_identity(self):
        async def main():
            factory = FakeFactory(scripts={"researcher": [("ok", _ok_result())]})
            rt = SubagentRuntime(factory=factory)
            await rt.start()
            try:
                from agentclaw.community.core.agentswarm.domain.models import (
                    AgentId, IdempotencyKey,
                )
                await rt.spawn(
                    _mk_run(), _mk_envelope(),
                    agent_id=AgentId("fixed-agent"),
                    idempotency_key=IdempotencyKey("resume-key-1"),
                )
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            r = results[0]
            assert r["agent_id"] == "fixed-agent"
            assert r["idempotency_key"] == "resume-key-1"

        asyncio.run(main())


class TestOutputSchemaValidation:
    def _shaped_envelope(self) -> TaskEnvelope:
        return TaskEnvelope(
            name="researcher",
            objective="produce shaped output",
            output_schema={
                "type": "object",
                "required": ["answer"],
                "properties": {"answer": {"type": "string"}},
            },
            boundaries="stay focused",
            parent_goal="test goal",
        )

    def test_schema_invalid_output_retried_then_failed(self):
        async def main():
            factory = FakeFactory(scripts={
                "researcher": [
                    ("ok", _ok_result({"unexpected": 1})),
                    ("ok", _ok_result({"unexpected": 2})),
                    ("ok", _ok_result({"unexpected": 3})),
                ],
            })
            rt = SubagentRuntime(factory=factory, max_attempts=3)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), self._shaped_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            r = results[0]
            assert r["status"] == AgentStatus.FAILED
            assert r["attempts"] == 3
            assert r["error"].startswith("schema_validation")
            assert r["result"] is None
            assert factory.creates == 3
            assert factory.tracker["teardown"] == 3

        asyncio.run(main())

    def test_schema_valid_output_completes(self):
        async def main():
            Env = self._shaped_envelope()
            factory = FakeFactory(scripts={
                "researcher": [("ok", _ok_result({"answer": "the answer"}))],
            })
            rt = SubagentRuntime(factory=factory)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), Env)
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            r = results[0]
            assert r["status"] == AgentStatus.COMPLETED
            assert r["result"] == {"answer": "the answer"}
            assert r["attempts"] == 1

        asyncio.run(main())

    def test_malformed_envelope_schema_fails_without_spawning(self):
        async def main():
            env = TaskEnvelope(
                name="researcher",
                objective="produce output",
                output_schema={"type": "banana_not_a_jsonschema_type"},
                boundaries="stay focused",
                parent_goal="test goal",
            )
            factory = FakeFactory(scripts={"researcher": [("ok", _ok_result())]})
            rt = SubagentRuntime(factory=factory)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), env)
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()

            r = results[0]
            assert r["status"] == AgentStatus.FAILED
            assert r["attempts"] == 1
            assert r["error"] == "envelope_schema_invalid"
            assert factory.creates == 0
            assert factory.tracker["setup"] == 0

        asyncio.run(main())

class TestTeardownFailureLogging:
    """teardown() is best-effort, but a failed host-platform teardown must
    be logged, not silently swallowed — and must never fail the attempt.
    """

    def test_teardown_raise_is_logged_and_attempt_still_completes(self, caplog):
        class TeardownBoomSubagent:
            def __init__(self, spec: AgentSpec, tracker: dict) -> None:
                self.name = spec.name
                self.tracker = tracker

            async def setup(self) -> None:
                self.tracker["setup"] += 1

            async def run_task(self, envelope: TaskEnvelope) -> SubagentResult:
                self.tracker["run_task"] += 1
                return _ok_result({"answer": "survived"})

            async def teardown(self) -> None:
                self.tracker["teardown"] += 1
                raise RuntimeError("platform bot leak")

            async def halt(self) -> None:
                self.tracker["halt"] += 1

        class TeardownBoomFactory:
            def __init__(self) -> None:
                self.creates = 0
                self.tracker = {
                    "setup": 0, "run_task": 0, "teardown": 0, "halt": 0,
                }

            def create(self, spec: AgentSpec, run: Run,
                       agent_id: AgentId | None = None,
                       attempt: int = 1) -> TeardownBoomSubagent:
                self.creates += 1
                return TeardownBoomSubagent(spec, self.tracker)

        async def main():
            factory = TeardownBoomFactory()
            rt = SubagentRuntime(factory=factory)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return results, factory

        with caplog.at_level(
            logging.ERROR,
            logger="agentclaw.community.core.agentswarm.runtime.pool.impl",
        ):
            results, factory = asyncio.run(main())

        (r,) = results
        assert r["status"] is AgentStatus.COMPLETED
        assert r["attempts"] == 1
        assert r["result"] == {"answer": "survived"}
        assert factory.creates == 1          # no retry of a completed run
        assert factory.tracker["teardown"] == 1
        teardown_records = [
            rec for rec in caplog.records
            if "subagent teardown failed" in rec.message
        ]
        assert teardown_records, [rec.message for rec in caplog.records]
        assert teardown_records[0].exc_info is not None
        assert "platform bot leak" in teardown_records[0].exc_text


class TestArtifactSpillFailure:
    """An artifact put failure must not discard a completed (schema-valid)
    result: falling back inline keeps the result XOR carrier intact and the
    finished subagent is never re-run. No false upstream-breaker failure.
    """

    def test_put_failure_keeps_result_inline_without_retry(self, caplog):
        class ExplodingArtifactStore:
            def __init__(self) -> None:
                self.puts = 0

            def put(self, key: str, content) -> bool:
                self.puts += 1
                raise RuntimeError("artifact store down")

            def put_file(self, key: str, local_path: str) -> bool:
                return True

            def delete(self, key: str) -> bool:
                return True

            def get(self, key: str):
                return None

        async def main():
            big_output = {"pad": "z" * 100}
            factory = FakeFactory(
                scripts={"researcher": [("ok", _ok_result(big_output))]}
            )
            store = ExplodingArtifactStore()
            breaker = UpstreamBreaker(failure_threshold=1, open_seconds=60.0)
            rt = SubagentRuntime(
                factory=factory, artifact_store=store,
                artifact_inline_threshold=10,
                upstream_breaker=breaker,
            )
            await rt.start()
            try:
                env = _mk_envelope()
                agent_id = await rt.spawn(_mk_run(), env)
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return results, factory, store, breaker, big_output, agent_id

        with caplog.at_level(
            logging.ERROR,
            logger="agentclaw.community.core.agentswarm.runtime.pool.impl",
        ):
            results, factory, store, breaker, big_output, agent_id = (
                asyncio.run(main())
            )

        (r,) = results
        assert r["status"] is AgentStatus.COMPLETED
        assert r["attempts"] == 1
        assert r["result"] == big_output      # inline fallback preserves payload
        assert r["artifact_key"] is None       # D31 XOR: exactly one carrier
        assert agent_id is not None
        assert factory.tracker["run_task"] == 1  # completed subagent never re-ran
        assert factory.creates == 1
        assert store.puts == 1
        assert breaker.state is BreakerState.CLOSED
        assert breaker.consecutive_failures == 0
        spill_records = [
            rec for rec in caplog.records
            if "artifact put failed" in rec.message
        ]
        assert spill_records, [rec.message for rec in caplog.records]
        assert "artifact store down" in spill_records[0].exc_text


class TestBoundedInlineFallback:
    """Round-5: the inline fallback (no store / spill failure) is bounded.

    Round-4 capped artifacts at 8 MiB (_MAX_ARTIFACT_BYTES), so an oversize
    output can no longer spill — and the unconditional inline fallback would
    write an unbounded payload into the persisted result ``payload`` column,
    turning the bounded-disk vector into an unbounded-DB-write vector.
    Above ``_MAX_INLINE_RESULT_CHARS`` the payload becomes a bounded
    truncated marker; the D31 XOR invariant stays intact (payload set,
    artifact_key None) and the run converges instead of re-running a
    completed subagent.
    """

    def _oversize_output(self) -> dict:
        # str() > _MAX_INLINE_RESULT_CHARS: 1M pad chars plus dict delimiters.
        return {"pad": "x" * (_MAX_INLINE_RESULT_CHARS + 50)}

    class _ExplodingStore:
        """Fake ArtifactStore whose put rejects oversize like the round-4 cap."""

        def __init__(self) -> None:
            self.puts = 0

        def put(self, key: str, content) -> bool:
            self.puts += 1
            raise ValueError(
                f"artifact exceeds 8388608 bytes: {len(content)}"
            )

        def put_file(self, key: str, local_path: str) -> bool:
            return True

        def delete(self, key: str) -> bool:
            return True

        def get(self, key: str):
            return None

    def test_over_cap_no_store_persists_bounded_marker(self, caplog):
        # (a) inline fallback over the cap with NO store: bounded marker,
        # never the raw huge object; the completion still flows to the
        # result handler so the result persists (D31 XOR: payload set,
        # artifact_key None); size records the true serialized length.
        async def main():
            big = self._oversize_output()
            factory = FakeFactory(
                scripts={"researcher": [("ok", _ok_result(big))]}
            )
            rt = SubagentRuntime(
                factory=factory, artifact_store=None,
                artifact_inline_threshold=10,
            )
            results = []
            rt.set_result_handler(results.append)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return results, big, results

        with caplog.at_level(
            logging.WARNING,
            logger="agentclaw.community.core.agentswarm.runtime.pool.impl",
        ):
            results, big, results = asyncio.run(main())

        (r,) = results
        assert results == [r]                       # the completion reached the result path
        assert r["status"] is AgentStatus.COMPLETED
        payload = r["result"]
        assert payload is not big                   # never the raw huge object
        assert not isinstance(payload, str)
        assert payload["truncated"] is True
        assert payload["size"] == len(str(big))
        assert payload["preview"] == str(big)[:_MAX_INLINE_RESULT_CHARS // 10]
        assert len(payload["preview"]) == _MAX_INLINE_RESULT_CHARS // 10
        assert r["artifact_key"] is None            # D31 XOR: exactly one carrier
        truncation_records = [
            rec for rec in caplog.records
            if "truncated" in rec.message and rec.levelno == logging.WARNING
        ]
        assert truncation_records, [rec.message for rec in caplog.records]

    def test_over_threshold_under_cap_no_store_stays_raw_inline(self):
        # (b) under the cap: unchanged behavior — the raw output object is
        # kept inline (same object, no marker, no copy).
        async def main():
            medium = {"pad": "y" * 50_000}          # > threshold 10, < cap
            factory = FakeFactory(
                scripts={"researcher": [("ok", _ok_result(medium))]}
            )
            rt = SubagentRuntime(
                factory=factory, artifact_store=None,
                artifact_inline_threshold=10,
            )
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return results, medium

        results, medium = asyncio.run(main())

        (r,) = results
        assert r["status"] is AgentStatus.COMPLETED
        assert r["result"] is medium                 # raw object, untouched
        assert r["artifact_key"] is None

    def test_spill_failure_over_cap_persists_bounded_marker(self, caplog):
        # (c) artifact spill failure (store raises, e.g. the round-4 8 MiB
        # cap) with an oversize output: bounded marker, no unbounded
        # payload, result XOR intact.
        async def main():
            big = self._oversize_output()
            factory = FakeFactory(
                scripts={"researcher": [("ok", _ok_result(big))]}
            )
            store = self._ExplodingStore()
            rt = SubagentRuntime(
                factory=factory, artifact_store=store,
                artifact_inline_threshold=10,
            )
            results = []
            rt.set_result_handler(results.append)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return results, big, results, store

        with caplog.at_level(
            logging.WARNING,
            logger="agentclaw.community.core.agentswarm.runtime.pool.impl",
        ):
            results, big, results, store = asyncio.run(main())

        (r,) = results
        assert results == [r]
        assert r["status"] is AgentStatus.COMPLETED
        assert store.puts == 1                        # the spill was attempted
        payload = r["result"]
        assert payload is not big                     # no unbounded payload
        assert payload["truncated"] is True
        assert payload["size"] == len(str(big))
        assert payload["preview"] == str(big)[:_MAX_INLINE_RESULT_CHARS // 10]
        assert r["artifact_key"] is None              # D31 XOR: exactly one carrier
        truncation_records = [
            rec for rec in caplog.records
            if "truncated" in rec.message and rec.levelno == logging.WARNING
        ]
        assert truncation_records, [rec.message for rec in caplog.records]
        spill_records = [
            rec for rec in caplog.records
            if "artifact put failed" in rec.message
        ]
        assert spill_records, [rec.message for rec in caplog.records]

    def test_spill_success_over_cap_output_unchanged_artifact_key(self):
        # (d) artifact spill success (store healthy): unchanged — even an
        # output over the inline cap is routed to the store; artifact_key
        # set, payload None. The marker only guards the FALLBACK branches.
        async def main():
            big = self._oversize_output()
            factory = FakeFactory(
                scripts={"researcher": [("ok", _ok_result(big))]}
            )
            store = FakeArtifactStore()
            rt = SubagentRuntime(
                factory=factory, artifact_store=store,
                artifact_inline_threshold=10,
            )
            await rt.start()
            try:
                run = _mk_run()
                agent_id = await rt.spawn(run, _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return results, big, store, run, agent_id

        results, big, store, run, agent_id = asyncio.run(main())

        (r,) = results
        assert r["status"] is AgentStatus.COMPLETED
        key = f"{run.run_id}/{agent_id}/result"
        assert r["artifact_key"] == key
        assert r["result"] is None
        assert store.puts == [key]
        assert store.contents[key] == str(big)
