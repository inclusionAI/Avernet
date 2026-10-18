"""Pool binding-journal tests (D-J/D-K/D-L Id): write-ahead intent, settle.

Covers: both executor kinds journal an intent BEFORE setup and settle
their identity after setup (bot carries the platform persona, llm the
bare digest), the in-slice attempt index and the slice's claim epoch
ride into every journal call, a replayed attempt is journaled under its
own row, pre-D-J doubles without executor surfaces journal nothing, a
blank executor_id journals the intent but never settles (the destroy
sweep reconciles such rows), a missing binding store is a no-op
composition, and a fenced-out journal unwinds the item as slice_lost
without feeding the upstream breaker.

Conventions follow test_subagent_runtime.py: sync test defs over asyncio.run,
hand-written fakes (no MagicMock).
"""
from __future__ import annotations

import asyncio
import time
import uuid

from agentclaw.community.core.agentswarm.domain.models import (
    AgentId, AgentKind, AgentSpec, EpochMillis, NonRetryableProvisioningError,
    ProviderKind, RequestMetadata, Run, RunId, RunStatus, SubagentResult,
    TaskEnvelope, TenantId,
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

    def __init__(self, spec: AgentSpec, executor_id: str,
                 order: list[str] | None = None) -> None:
        self.name = spec.name
        self.executor_id = executor_id
        # Mirrors the platform persona naming: the 12-char agent_swarm_
        # prefix rides into the display name trailed by its digest8.
        self.executor_name = f"{spec.name}-{executor_id[:20]}" if executor_id \
            else spec.name
        self._order = order

    async def setup(self) -> None:
        if self._order is not None:
            self._order.append("setup")

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

    def __init__(self, blank_id: bool = False, bare: bool = False,
                 order: list[str] | None = None) -> None:
        self.created_attempts: list[int] = []
        self.blank_id = blank_id
        self.bare = bare
        self.order = order

    def create(self, spec, run, agent_id=None, attempt: int = 1):
        self.created_attempts.append(attempt)
        if self.bare:
            return BareSubagent(spec)
        executor_id = "" if self.blank_id else f"agent_swarm_{uuid.uuid4().hex[:16]}"
        return ExecutorSubagent(spec, executor_id, order=self.order)


class RecordingBindingStore:
    """SliceStore double for the two calls the pool makes: the
    write-ahead intent (pre-setup) and the settle (post-setup)."""

    def __init__(self, fail_intent_with: Exception | None = None) -> None:
        self.intent_calls: list[dict] = []
        self.binding_calls: list[dict] = []
        self.result_calls: list[dict] = []
        self.fail_intent_with = fail_intent_with

    async def journal_attempt_intent(self, run, agent_id, claim_epoch,
                                      attempt, executor_kind, executor_id,
                                      executor_name):
        self.intent_calls.append({
            "run_id": run.run_id,
            "agent_id": agent_id,
            "claim_epoch": claim_epoch,
            "attempt": attempt,
            "executor_kind": executor_kind,
            "executor_id": executor_id,
            "executor_name": executor_name,
        })
        if self.fail_intent_with is not None:
            raise self.fail_intent_with

    async def record_agent_binding(self, run, agent_id, claim_epoch, attempt,
                                    executor_kind, executor_id, executor_name):
        self.binding_calls.append({
            "run_id": run.run_id,
            "agent_id": agent_id,
            "claim_epoch": claim_epoch,
            "attempt": attempt,
            "executor_kind": executor_kind,
            "executor_id": executor_id,
            "executor_name": executor_name,
        })

    async def journal_attempt_result(self, run, agent_id, claim_epoch, attempt,
                                     result_status, result_message) -> None:
        self.result_calls.append({
            "run": run,
            "agent_id": agent_id,
            "claim_epoch": claim_epoch,
            "attempt": attempt,
            "result_status": result_status,
            "result_message": result_message,
        })


class TestJournalIntentAndSettle:
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
        # One intent + one settle per attempt, under the slice's epoch.
        assert len(store.intent_calls) == len(store.binding_calls) == 2
        by_kind = {c["executor_kind"]: c for c in store.binding_calls}
        assert set(by_kind) == {AgentKind.BOT.value, AgentKind.LLM.value}
        bot, llm = by_kind[AgentKind.BOT.value], by_kind[AgentKind.LLM.value]
        assert bot["executor_id"].startswith("agent_swarm_")
        assert bot["claim_epoch"] == 41
        assert bot["executor_name"].startswith("scout-agent_swarm_")
        assert llm["claim_epoch"] == 41
        assert all(c["executor_id"].startswith("agent_swarm_")
                   for c in store.intent_calls)

    def test_intent_lands_before_setup_settle_after(self):
        async def main():
            order: list[str] = []
            store = RecordingBindingStore()

            def note(kind):
                order.append(kind)

            class OrderingStore(RecordingBindingStore):
                async def journal_attempt_intent(self, *a, **k):
                    note("intent")
                    return await super().journal_attempt_intent(*a, **k)

                async def record_agent_binding(self, *a, **k):
                    note("settle")
                    return await super().record_agent_binding(*a, **k)

            factory = JournalingFactory(order=order)
            rt = SubagentRuntime(
                factory=factory, binding_store=OrderingStore())
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return order, results

        order, results = asyncio.run(main())
        assert results[0]["status"].value == "COMPLETED"
        assert order == ["intent", "setup", "settle"]

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
        assert [c["attempt"] for c in store.intent_calls] == [1, 2]
        assert [c["executor_id"] for c in store.binding_calls] == [
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
        assert store.intent_calls == []
        assert store.binding_calls == []
        assert results[0]["status"].value == "COMPLETED"

    def test_blank_executor_id_journals_intent_but_never_settles(self):
        # The bot-kind pre-setup shape: the persona id does not exist yet,
        # so the intent row carries a blank executor_id — the exact row the
        # terminal destroy sweep must reconcile. Setup surfacing no
        # identity skips the settle (nothing platform-backed to record).
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
        (intent,) = store.intent_calls
        assert intent["executor_id"] == ""
        assert intent["executor_name"] == "scout"
        assert store.binding_calls == []
        assert results[0]["status"].value == "COMPLETED"

    def test_success_journals_completed_result(self):
        async def main():
            store = RecordingBindingStore()
            rt = SubagentRuntime(
                factory=JournalingFactory(), binding_store=store)
            rt.set_claim_epoch(7)
            await rt.start()
            try:
                run = _mk_run()
                await rt.spawn(run, _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return store, run, results

        store, run, results = asyncio.run(main())
        assert results[0]["status"].value == "COMPLETED"
        (call,) = store.result_calls
        assert call["result_status"] == "COMPLETED"
        assert call["result_message"] is None
        assert call["run"] is run
        assert call["agent_id"] is not None
        assert call["attempt"] == 1
        assert call["claim_epoch"] == 7

    def test_non_retryable_failure_journals_failed_result(self):
        async def main():
            class FailingFactory(JournalingFactory):
                def create(self, spec, run, agent_id=None, attempt: int = 1):
                    self.created_attempts.append(attempt)
                    sub = ExecutorSubagent(spec, f"ac_fail{attempt}")

                    async def boom(envelope):
                        raise NonRetryableProvisioningError("quota exceeded")

                    sub.run_task = boom
                    return sub

            store = RecordingBindingStore()
            rt = SubagentRuntime(
                factory=FailingFactory(), binding_store=store, max_attempts=3)
            rt.set_claim_epoch(9)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return store, results

        store, results = asyncio.run(main())
        (r,) = results
        assert r["status"].value == "FAILED"
        # A deterministic provisioning failure fails fast: one attempt, one
        # FAILED result journal carrying the error text.
        assert r["attempts"] == 1
        call = store.result_calls[-1]
        assert call["result_status"] == "FAILED"
        assert "quota exceeded" in call["result_message"]
        assert call["attempt"] == 1
        assert call["claim_epoch"] == 9

    def test_slice_lost_unwind_journals_slice_lost_result(self):
        async def main():
            factory = JournalingFactory()
            store = RecordingBindingStore(fail_intent_with=SliceLost())
            rt = SubagentRuntime(
                factory=factory, binding_store=store, max_attempts=3)
            rt.set_claim_epoch(5)
            await rt.start()
            try:
                run = _mk_run()
                await rt.spawn(run, _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return store, run, results

        store, run, results = asyncio.run(main())
        (r,) = results
        assert r["status"].value == "FAILED"
        assert r["error"] == "slice_lost"
        # The fenced-out attempt journals its own SLICE_LOST outcome (the
        # unwind's terminal leg), distinct from the plain FAILED branches.
        slice_lost_calls = [
            c for c in store.result_calls if c["result_status"] == "SLICE_LOST"
        ]
        assert slice_lost_calls, "a slice_lost unwind still journals a SLICE_LOST"
        assert all(c["result_status"] == "SLICE_LOST" for c in slice_lost_calls)
        assert all(c["result_message"] == "slice_lost" for c in slice_lost_calls)
        call = slice_lost_calls[0]
        assert call["run"] is run
        assert call["attempt"] == 1
        assert call["claim_epoch"] == 5

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
        # D28 at the write-ahead journal: a fenced-out binding write
        # unwinds the item as slice_lost on the attempt loop — the
        # persona's own teardown still runs, the item ends FAILED
        # slice_lost, and the upstream breaker stays clean (a lost claim
        # is not upstream's failure).
        async def main():
            factory = JournalingFactory()
            store = RecordingBindingStore(fail_intent_with=SliceLost())
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
        # One persona minted (attempt 1), the fence rejected its intent
        # journal before setup, and attempt 2 exits at the slice_lost
        # check before any second subagent is constructed — a dead
        # claim never mints fresh personas.
        assert factory.created_attempts == [1]
        assert len(store.intent_calls) == 1 and \
            store.intent_calls[0]["claim_epoch"] == 5
        assert store.binding_calls == []
        assert breaker.state is BreakerState.CLOSED
        assert breaker.consecutive_failures == 0