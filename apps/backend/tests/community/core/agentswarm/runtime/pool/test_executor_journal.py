"""Pool binding-journal tests (D-J/D-K/D-L Id): journal after setup per kind.

Covers: both executor kinds journal their identity after setup (bot carries
the platform persona, llm the bare digest), the in-slice attempt index and
the slice's claim epoch ride into the journal call, a replayed attempt is
journaled under its own row, pre-D-J doubles without executor surfaces or a
blank id journal nothing, a missing binding store is a no-op composition, and
a fenced-out journal unwinds the item as slice_lost without feeding the
upstream breaker.

Conventions follow test_subagent_runtime.py: sync test defs over asyncio.run,
hand-written fakes (no MagicMock).
"""
from __future__ import annotations

import asyncio
import time
import uuid

from agentclaw.community.core.agentswarm.domain.models import (
    AgentId, AgentKind, AgentSpec, EpochMillis, ProviderKind, RequestMetadata,
    Run, RunId, RunStatus, SubagentResult, TaskEnvelope, TenantId,
)
from agentclaw.community.core.agentswarm.runtime.breaker import (
    BreakerState, UpstreamBreaker,
)
from agentclaw.community.core.agentswarm.runtime.pool import SubagentRuntime
from agentclaw.community.core.agentswarm.runtime.slice_lost import SliceLost


def _now_ms():
    return int(time.time() * 1000)


def _mk_run(catalog=None) -> Run:
    return Run(
        run_id=RunId("run-" + uuid.uuid4().hex[:8]),
        tenant=TenantId("tenant-a"),
        goal="journal goal",
        agent_catalog=tuple(catalog) if catalog else (
            AgentSpec(name="scout", role="worker", kind=AgentKind.BOT),
            AgentSpec(name="clerk", role="research"),
        ),
        provider=ProviderKind("stub"),
        request_metadata=RequestMetadata(),
        status=RunStatus.RUNNING,
        started_at=EpochMillis(_now_ms()),
    )


def _mk_envelope(name="scout") -> TaskEnvelope:
    return TaskEnvelope(
        name=name, objective="do the thing", output_schema={}, boundaries="",
        parent_goal="journal goal",
    )


def _ok_result() -> SubagentResult:
    return SubagentResult(output={"done": True})


class ExecutorSubagent:
    """Subagent double exposing the additive executor surfaces (D-J)."""

    def __init__(self, spec: AgentSpec, executor_id: str) -> None:
        self.name = spec.name
        self.executor_id = executor_id
        self.executor_name = f"{spec.name}-{executor_id[:8]}"

    async def setup(self) -> None:
        pass

    async def run_task(self, envelope: TaskEnvelope) -> SubagentResult:
        return _ok_result()

    async def teardown(self) -> None:
        pass

    async def halt(self) -> None:
        pass


class BareSubagent:
    """Pre-D-J double: no executor surfaces at all."""

    def __init__(self, spec: AgentSpec) -> None:
        self.name = spec.name

    async def setup(self) -> None:
        pass

    async def run_task(self, envelope: TaskEnvelope) -> SubagentResult:
        return _ok_result()

    async def teardown(self) -> None:
        pass

    async def halt(self) -> None:
        pass


class JournalingFactory:
    """Builds per-kind executor doubles and records create attempts."""

    def __init__(self, blank_id: bool = False, bare: bool = False) -> None:
        self.created_attempts: list[int] = []
        self.blank_id = blank_id
        self.bare = bare

    def create(self, spec, run, agent_id=None, attempt: int = 1):
        self.created_attempts.append(attempt)
        if self.bare:
            return BareSubagent(spec)
        executor_id = "" if self.blank_id else f"ac_{uuid.uuid4().hex[:16]}"
        return ExecutorSubagent(spec, executor_id)


class RecordingBindingStore:
    """SliceStore double for the one call the pool makes (D-L)."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.fail_with = fail_with

    async def record_agent_binding(self, run, agent_id, claim_epoch, attempt,
                                    executor_kind, executor_id, executor_name):
        self.calls.append({
            "run_id": run.run_id,
            "agent_id": agent_id,
            "claim_epoch": claim_epoch,
            "attempt": attempt,
            "executor_kind": executor_kind,
            "executor_id": executor_id,
            "executor_name": executor_name,
        })
        if self.fail_with is not None:
            raise self.fail_with


def _drain(rt: SubagentRuntime) -> list[dict]:
    return asyncio.run(rt.wait_for_completions())


class TestJournalAfterSetup:
    def test_both_kinds_journal_their_identity(self):
        async def main():
            store = RecordingBindingStore()
            factory = JournalingFactory()
            rt = SubagentRuntime(factory=factory, binding_store=store)
            rt.set_claim_epoch(41)
            await rt.start()
            try:
                run = _mk_run()
                await rt.spawn(run, _mk_envelope(name="scout"))
                await rt.spawn(run, _mk_envelope(name="clerk"))
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return store, run, results

        store, run, results = asyncio.run(main())
        assert len(results) == 2 and all(
            r["status"].value == "COMPLETED" for r in results)
        by_kind = {c["executor_kind"]: c for c in store.calls}
        assert set(by_kind) == {AgentKind.BOT.value, AgentKind.LLM.value}
        bot, llm = by_kind[AgentKind.BOT.value], by_kind[AgentKind.LLM.value]
        assert bot["executor_id"].startswith("ac_")
        assert bot["executor_kind"] == AgentKind.BOT.value
        assert bot["claim_epoch"] == 41
        assert bot["executor_name"].startswith("scout-ac_")
        assert llm["claim_epoch"] == 41

    def test_retry_attempts_journal_under_their_own_row(self):
        async def main():
            class FlakyFactory(JournalingFactory):
                def create(self, spec, run, agent_id=None, attempt: int = 1):
                    self.created_attempts.append(attempt)
                    sub = ExecutorSubagent(spec, f"ac_attempt{attempt}xyz")
                    if attempt == 1:

                        async def boom(envelope):
                            raise RuntimeError("transient")

                        sub.run_task = boom
                    return sub

            store = RecordingBindingStore()
            rt = SubagentRuntime(factory=FlakyFactory(), binding_store=store,
                                 max_attempts=3)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return store, results

        store, results = asyncio.run(main())
        (r,) = results
        assert r["status"].value == "COMPLETED"
        assert r["attempts"] == 2
        assert [c["attempt"] for c in store.calls] == [1, 2]
        assert [c["executor_id"] for c in store.calls] == [
            "ac_attempt1xyz", "ac_attempt2xyz"]

    def test_pre_dj_double_without_executor_surfaces_journals_nothing(self):
        async def main():
            store = RecordingBindingStore()
            rt = SubagentRuntime(
                factory=JournalingFactory(bare=True), binding_store=store)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return store, results

        store, results = asyncio.run(main())
        assert store.calls == []
        assert results[0]["status"].value == "COMPLETED"

    def test_blank_executor_id_journals_nothing(self):
        async def main():
            store = RecordingBindingStore()
            rt = SubagentRuntime(
                factory=JournalingFactory(blank_id=True),
                binding_store=store)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return store, results

        store, results = asyncio.run(main())
        assert store.calls == []
        assert results[0]["status"].value == "COMPLETED"

    def test_missing_binding_store_is_a_no_op_composition(self):
        async def main():
            rt = SubagentRuntime(factory=JournalingFactory())
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return results

        results = asyncio.run(main())
        assert results[0]["status"].value == "COMPLETED"


class TestFencedJournalUnwinds:
    def test_slice_lost_journal_ends_item_without_feeding_breaker(self):
        # D28 at the journal: a fenced-out binding write unwinds the item
        # as slice_lost on the attempt loop — the persona's own teardown
        # still runs, the item ends FAILED slice_lost, and the upstream
        # breaker stays clean (a lost claim is not upstream's failure).
        async def main():
            factory = JournalingFactory()
            store = RecordingBindingStore(fail_with=SliceLost())
            breaker = UpstreamBreaker(failure_threshold=1, open_seconds=60.0)
            rt = SubagentRuntime(
                factory=factory, binding_store=store,
                max_attempts=3, upstream_breaker=breaker,
            )
            rt.set_claim_epoch(5)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return factory, store, results, breaker

        factory, store, results, breaker = asyncio.run(main())
        (r,) = results
        assert r["status"].value == "FAILED"
        assert r["attempts"] == 2
        assert r["error"] == "slice_lost"
        # One persona minted (attempt 1), the fence rejected its journal,
        # and attempt 2 exits at the slice_lost check before any second
        # subagent is constructed — a dead claim never mints fresh personas.
        assert factory.created_attempts == [1]
        assert len(store.calls) == 1 and store.calls[0]["claim_epoch"] == 5
        assert breaker.state is BreakerState.CLOSED
        assert breaker.consecutive_failures == 0