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
import time
import uuid
from collections import deque

import pytest

from agentclaw.community.core.agentcompute.domain.models import (
    AgentSpecRaw, AgentStatus, EpochMillis, ProviderId, RequestMetadata,
    Run, RunId, RunStatus, SubagentResult, TaskEnvelope, TenantId,
    TerminationReason, TokenUsage,
)
from agentclaw.community.core.agentcompute.runtime.pool import (
    SubagentRuntime,
)


def _now_ms():
    return int(time.time() * 1000)


def _mk_run(catalog=None) -> Run:
    return Run(
        run_id=RunId("run-" + uuid.uuid4().hex[:8]),
        tenant=TenantId("tenant-a"),
        goal="test goal",
        agent_catalog=tuple(catalog) if catalog else (
            AgentSpecRaw(name="researcher", role="deep research"),
        ),
        provider=ProviderId("stub"),
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

    def __init__(self, spec: AgentSpecRaw, script: deque, tracker: dict,
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
        self.tracker = {
            "setup": 0, "run_task": 0, "teardown": 0, "halt": 0,
            "cancelled": 0, "concurrent": 0, "max_concurrent": 0,
            "envelopes": [],
        }

    def create(self, spec: AgentSpecRaw, run: Run) -> ScriptedSubagent:
        self.creates += 1
        self.created_specs.append(spec)
        self.created_runs.append(run)
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
                def create(self, spec, run):
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
        # (shard XOR carrier, D31) instead of dropping it.
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
            catalog_spec = AgentSpecRaw(
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
            assert factory.created_specs[0] == AgentSpecRaw(
                name="ghost", role="the objective"
            )

        asyncio.run(main())


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
                from agentclaw.community.core.agentcompute.domain.models import (
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