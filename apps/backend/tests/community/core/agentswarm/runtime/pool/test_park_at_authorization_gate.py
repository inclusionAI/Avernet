"""Human-gate park semantics in the pool retry loop.

Covers the pool's side of the human gate: a
``ParkedAwaitingAuthorization`` raised by setup is a wait state — no
timeout journal, no failed-attempt increment, no new attempt key or
persona — rechecked cheaply on an interval and resumed as the SAME
attempt on issuance; park time never trips the setup clock; the
authorization's terminal answers (human-rejected / window-elapsed) fail
the attempt exactly once through the non-retryable treatment; an
auto-issued (fast-path) item runs the pre-change flow untouched beside
a parked one; and a worker cancelled while parked leaves the awaiting
persona alive for the next slice's D16 respawn instead of discarding
the pending authorization.

Conventions follow test_subagent_runtime.py / test_non_retryable.py:
sync test defs driving async code through asyncio.run, hand-written
fakes (no MagicMock).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from agentclaw.community.core.agentswarm.domain.models import (
    AgentId, AgentSpec, AgentStatus, EpochMillis, NonRetryableProvisioningError,
    ProviderKind, RequestMetadata, Run, RunId, RunStatus, SubagentResult,
    TaskEnvelope, TenantId,
)
from agentclaw.community.core.agentswarm.runtime.breaker import (
    BreakerState, UpstreamBreaker,
)
from agentclaw.community.core.agentswarm.runtime.pool import SubagentRuntime
from agentclaw.community.plugin_api.agentswarm.subagent import (
    ParkedAwaitingAuthorization,
)

_PARK_STATUS = "PARKED_AWAITING_AUTHORIZATION"
_PARK_RESULT_LABEL = f"result:{_PARK_STATUS}"


def _now_ms():
    return int(time.time() * 1000)


def _mk_run(catalog=None) -> Run:
    return Run(
        run_id=RunId("run-" + uuid.uuid4().hex[:8]),
        tenant=TenantId("tenant-a"),
        goal="park goal",
        agent_catalog=tuple(catalog) if catalog else (
            AgentSpec(name="gatebot", role="worker"),
        ),
        provider=ProviderKind("stub"),
        request_metadata=RequestMetadata(),
        status=RunStatus.RUNNING,
        started_at=EpochMillis(_now_ms()),
    )


def _mk_envelope(name="gatebot") -> TaskEnvelope:
    return TaskEnvelope(
        name=name, objective="do the thing", output_schema={}, boundaries="",
        parent_goal="park goal",
    )


def park(msg="bot bot-42 parked awaiting human authorization "
             "(window deadline 2026-10-23T12:00:00+00:00)"):
    return ("raise", ParkedAwaitingAuthorization(msg, bot_id="bot-42"))


def _ok_result() -> SubagentResult:
    return SubagentResult(output={"done": True})


class GateSubagent:
    """Script-driven Subagent double: every setup() call pops one entry.

    ``("raise", exc)`` raises; ``("sleep", seconds)`` provisions slowly
    (exercises the pool's setup clock); ``("hold", event)`` awaits the
    event then settles (deterministic-release orderings); anything else
    completes setup and settles the executor surfaces exactly like
    AvernetSubagent does.
    """

    def __init__(self, spec: AgentSpec, outcomes: list, tracker: dict) -> None:
        self.name = spec.name
        self.executor_id = ""
        self.executor_name = spec.name
        # Shared by reference: pops decrement the factory's script so a
        # same-item retry consumes the NEXT entry, never a replay.
        self._outcomes = outcomes
        self._tracker = tracker

    async def setup(self) -> None:
        self._tracker["setup_calls"] += 1
        if self._outcomes:
            entry = self._outcomes.pop(0)
        else:
            entry = ("ok", None)
        kind, arg = entry[0], entry[1]
        if kind == "raise":
            raise arg
        if kind == "sleep":
            await asyncio.sleep(arg)
        if kind == "hold":
            await arg.wait()
        self.executor_id = f"agent_swarm_{self.name}"
        self.executor_name = f"{self.name}-persona"

    async def run_task(self, envelope: TaskEnvelope) -> SubagentResult:
        return _ok_result()

    async def teardown(self) -> None:
        self._tracker["teardown"] += 1

    async def halt(self) -> None:
        pass


class GateFactory:
    """One shared tracker + per-name setup script; records attempts."""

    def __init__(self, scripts: dict) -> None:
        self.scripts = scripts
        self.created_attempts: list[int] = []
        self.tracker = {"setup_calls": 0, "teardown": 0}

    def create(self, spec, run, agent_id=None, attempt: int = 1):
        self.created_attempts.append(attempt)
        return GateSubagent(spec, self.scripts[spec.name], self.tracker)


class JournalStore:
    """SliceStore double recording every journal call in arrival order."""

    def __init__(self) -> None:
        self.intent_calls: list[dict] = []
        self.binding_calls: list[dict] = []
        self.result_calls: list[dict] = []
        self.bot_state_calls: list[dict] = []
        self.order: list[tuple[str, str]] = []

    async def journal_attempt_intent(self, run, agent_id, claim_epoch,
                                     attempt, executor_kind, executor_id,
                                     executor_name):
        self.intent_calls.append({"agent_id": agent_id, "attempt": attempt})
        self.order.append(("intent", str(agent_id)))

    async def record_agent_binding(self, run, agent_id, claim_epoch, attempt,
                                    executor_kind, executor_id, executor_name):
        self.binding_calls.append({"agent_id": agent_id, "attempt": attempt})
        self.order.append(("binding", str(agent_id)))

    async def journal_attempt_result(self, run, agent_id, claim_epoch,
                                     attempt, result_status, result_message):
        self.result_calls.append({
            "agent_id": agent_id, "attempt": attempt,
            "status": result_status, "message": result_message,
        })
        self.order.append((f"result:{result_status}", str(agent_id)))

    async def record_agent_bot_state(self, run, agent_id, claim_epoch,
                                     bot_id, bot_state):
        self.bot_state_calls.append({
            "agent_id": agent_id, "bot_id": bot_id,
            "bot_state": bot_state,
        })
        self.order.append((f"bot_state:{bot_state}", str(agent_id)))


def _statuses(store: JournalStore, agent_id) -> list[str]:
    return [c["status"] for c in store.result_calls
            if str(c["agent_id"]) == str(agent_id)]


async def _drive(rt: SubagentRuntime, run: Run, envelope: TaskEnvelope):
    await rt.start()
    try:
        agent_id = await rt.spawn(run, envelope)
        results = await rt.wait_for_completions()
        return agent_id, results
    finally:
        await rt.stop()


class TestParkedIsWaitStateResumesSameAttempt:

    def test_park_then_issuance_resumes_the_same_attempt(self, caplog):
        # Two park rechecks, then issuance: the SAME subagent instance is
        # re-driven three times, yet the factory built exactly one attempt,
        # the ledger shows the gate transition once (never FAILED/TIMEOUT),
        # and the completion reports attempt 1.
        factory = GateFactory({"gatebot": [
            park(), park(), ("ok", None),
        ]})
        store = JournalStore()
        breaker = UpstreamBreaker(failure_threshold=1, open_seconds=60.0)

        async def main():
            rt = SubagentRuntime(
                factory=factory, max_attempts=3, upstream_breaker=breaker,
                binding_store=store, park_recheck_interval=0.02,
            )
            return await _drive(rt, _mk_run(), _mk_envelope())

        with caplog.at_level(logging.INFO, logger="agentswarm"):
            agent_id, results = asyncio.run(main())

        (r,) = results
        assert r["status"] is AgentStatus.COMPLETED
        assert r["attempts"] == 1
        assert str(r["idempotency_key"]).startswith(f"{r['run_id']}:")
        # One attempt persona across park→recheck→recheck→resume: the park
        # never minted a replacement (no new factory.create, no teardown of
        # the awaiting persona mid-park).
        assert factory.created_attempts == [1]
        assert factory.tracker["setup_calls"] == 3
        assert factory.tracker["teardown"] == 1
        assert _statuses(store, agent_id) == [_PARK_STATUS, "COMPLETED"]
        assert len(store.intent_calls) == 1
        assert store.binding_calls[0]["attempt"] == 1
        # The gate is durable before the rechecks: repeated fenced writes of the
        # park state (bot identity + AWAITING_AUTHORIZATION) land on the
        # agent row so run detail and the SSE gate delta can express it --
        # concurrent workers' first write can be silently lost, so each
        # recheck cycle repeats the idempotent UPDATE.
        assert store.bot_state_calls == [{
            "agent_id": agent_id, "bot_id": "bot-42",
            "bot_state": "AWAITING_AUTHORIZATION",
        }] * 2  # once per park recheck cycle
        # The wait state fed no failure signal upward.
        assert breaker.state is BreakerState.CLOSED
        assert breaker.consecutive_failures == 0
        parks = [rec for rec in caplog.records
                 if "parked at authorization gate" in rec.getMessage()]
        assert parks and "bot-42" in parks[0].getMessage()

    def test_parked_wait_longer_than_setup_timeout_never_times_out(self):
        # D5: parked time is excluded from the setup clock. The item parks
        # for six recheck intervals (well past a 0.05s setup budget) and
        # still completes on attempt 1 with no TIMEOUT entry anywhere.
        factory = GateFactory({"gatebot": [
            park(), park(), park(), park(), park(), park(), ("ok", None),
        ]})
        store = JournalStore()

        async def main():
            rt = SubagentRuntime(
                factory=factory, max_attempts=2, setup_timeout=0.05,
                binding_store=store, park_recheck_interval=0.02,
            )
            return await _drive(rt, _mk_run(), _mk_envelope())

        agent_id, results = asyncio.run(main())

        (r,) = results
        assert r["status"] is AgentStatus.COMPLETED
        assert r["attempts"] == 1
        assert factory.created_attempts == [1]
        assert _statuses(store, agent_id) == [_PARK_STATUS, "COMPLETED"]

    def test_cancelled_while_parked_keeps_the_awaiting_persona(self):
        # Slice boundary mid-park: the worker is cancelled (handle_slice's
        # runtime.stop()), and the parked attempt must not tear its persona
        # down — deleting it would discard the pending authorization, so
        # the next slice's D16 respawn would open a fresh gate. No
        # completion, no attempt burn, no failure journal.
        factory = GateFactory({"gatebot": [park()] * 100})
        store = JournalStore()

        async def main():
            rt = SubagentRuntime(
                factory=factory, max_attempts=3, binding_store=store,
                park_recheck_interval=0.02,
            )
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                deadline = time.monotonic() + 2.0
                while not store.result_calls and time.monotonic() < deadline:
                    await asyncio.sleep(0.005)
                assert store.result_calls, "park journal never landed"
                # The slice boundary: cancel the workers mid-park exactly
                # the way handle_slice's finally does (runtime.stop()).
                await rt.stop()
                remaining = await rt.wait_for_completions(timeout=0.02)
                return remaining
            finally:
                await rt.stop()

        remaining = asyncio.run(main())

        assert remaining == []
        assert store.result_calls[0]["status"] == _PARK_STATUS
        statuses = [c["status"] for c in store.result_calls]
        assert "TIMEOUT" not in statuses and "FAILED" not in statuses
        # The gate entry burned no attempts, and the awaiting persona was
        # never touched by teardown.
        assert factory.created_attempts == [1]
        assert factory.tracker["teardown"] == 0


class TestTerminalAuthorizationOutcomesFailOnce:

    def _run_gate_answer(self, terminal: NonRetryableProvisioningError):
        factory = GateFactory({"gatebot": [park(), ("raise", terminal)]})
        store = JournalStore()
        breaker = UpstreamBreaker(failure_threshold=1, open_seconds=60.0)

        async def main():
            rt = SubagentRuntime(
                factory=factory, max_attempts=3, upstream_breaker=breaker,
                binding_store=store, park_recheck_interval=0.02,
            )
            return await _drive(rt, _mk_run(), _mk_envelope())

        agent_id, results = asyncio.run(main())
        return factory, store, agent_id, results

    def test_human_rejected_fails_exactly_once_non_retryable(self):
        factory, store, agent_id, results = self._run_gate_answer(
            NonRetryableProvisioningError(
                "bot bot-42 creation failed: AUTHORIZATION_REJECTED",
                bot_id="bot-42", bot_state="AUTHORIZATION_REJECTED"))
        (r,) = results
        assert r["status"] is AgentStatus.FAILED
        assert r["attempts"] == 1
        assert "AUTHORIZATION_REJECTED" in r["error"]
        assert "NonRetryableProvisioningError" in r["error"]
        # Exactly one attempt — the rejection was never retried with a
        # fresh persona, and the LLM breaker was never fed a human's no.
        assert factory.created_attempts == [1]
        # The trailing FAILED is the pre-change post-loop journal write
        # (attempts_used recap); the gate itself never journaled TIMEOUT.
        assert _statuses(store, agent_id) == [
            _PARK_STATUS, "FAILED", "FAILED"]
        # The human's verdict is durable: the agent row leaves the gate
        # via the terminal authorization state, so the run surface stops
        # serving the consent URL of a bot no human will confirm.
        assert store.bot_state_calls[-1] == {
            "agent_id": agent_id, "bot_id": "bot-42",
            "bot_state": "AUTHORIZATION_REJECTED",
        }

    def test_window_elapsed_fails_exactly_once_non_retryable(self):
        factory, store, agent_id, results = self._run_gate_answer(
            NonRetryableProvisioningError(
                "bot bot-42 creation failed: AUTHORIZATION_EXPIRED",
                bot_id="bot-42", bot_state="AUTHORIZATION_EXPIRED"))
        (r,) = results
        assert r["status"] is AgentStatus.FAILED
        assert r["attempts"] == 1
        assert "AUTHORIZATION_EXPIRED" in r["error"]
        assert factory.created_attempts == [1]
        assert _statuses(store, agent_id) == [
            _PARK_STATUS, "FAILED", "FAILED"]
        assert store.bot_state_calls[-1]["bot_state"] == "AUTHORIZATION_EXPIRED"

    def test_retryable_error_after_park_still_burns_budget_normally(self):
        # The park carve-out must not leak into ordinary failure handling:
        # a post-park retryable error resumes the pre-change retry loop
        # (fresh attempt, fresh persona) and eventually tears down. The
        # FAILED-per-attempt plus final recap is the pre-change journal
        # dance; only the leading PARKED entry is park-carried.
        factory = GateFactory({"gatebot": [park(), ("raise", RuntimeError(
            "transient boom")), ("raise", RuntimeError("boom again"))]})
        store = JournalStore()

        async def main():
            rt = SubagentRuntime(
                factory=factory, max_attempts=2, binding_store=store,
                park_recheck_interval=0.02,
            )
            return await _drive(rt, _mk_run(), _mk_envelope())

        agent_id, results = asyncio.run(main())
        (r,) = results
        assert r["status"] is AgentStatus.FAILED
        assert r["attempts"] == 2
        assert r["error"] == "boom again"
        assert factory.created_attempts == [1, 2]
        assert _statuses(store, agent_id) == [
            _PARK_STATUS, "FAILED", "FAILED", "FAILED"]
        assert factory.tracker["teardown"] == 2

    def test_slow_provisioning_after_park_is_still_setup_clocked(self):
        # D5's converse: only parked time is excluded. Provisioning that
        # actually runs (a slow setup continuation) consumes the setup
        # clock and fails through the pre-change TIMEOUT path (the final
        # FAILED recap follows, as it always did).
        factory = GateFactory({"gatebot": [park(), ("sleep", 1.0)]})
        store = JournalStore()

        async def main():
            rt = SubagentRuntime(
                factory=factory, max_attempts=1, setup_timeout=0.03,
                binding_store=store, park_recheck_interval=0.02,
            )
            return await _drive(rt, _mk_run(), _mk_envelope())

        agent_id, results = asyncio.run(main())
        (r,) = results
        assert r["status"] is AgentStatus.FAILED
        assert r["error"] == "timeout"
        assert _statuses(store, agent_id) == [
            _PARK_STATUS, "TIMEOUT", "FAILED"]
        assert factory.tracker["teardown"] == 1


class TestFastPathIndependence:

    def test_auto_issued_fast_path_never_parks_beside_a_parked_item(self):
        # D7: the gate derives from the actual authorization status, per
        # bot. One item (auto-issued) runs the pre-change flow untouched —
        # one setup call, no park journal, no attempt-key movement — while
        # the other sits at the gate; the two never interact. The fast
        # item's setup is held on an event until the parked item is
        # observably at the gate, so "the gate held the whole width"
        # cannot explain its completion.
        async def main():
            release_fast = asyncio.Event()
            factory = GateFactory({
                "fastbot": [("hold", release_fast)],
                "gatebot": [park()] * 20 + [("ok", None)],
            })
            store = JournalStore()
            run = _mk_run(catalog=(
                AgentSpec(name="fastbot", role="worker"),
                AgentSpec(name="gatebot", role="worker"),
            ))
            rt = SubagentRuntime(
                factory=factory, max_attempts=3, binding_store=store,
                park_recheck_interval=0.02, max_width=2,
            )
            await rt.start()
            try:
                fast_id = AgentId(await rt.spawn(run, _mk_envelope("fastbot")))
                gate_id = AgentId(await rt.spawn(run, _mk_envelope("gatebot")))
                deadline = time.monotonic() + 2.0
                while (not any(c["agent_id"] == gate_id for c in
                               store.result_calls)
                       and time.monotonic() < deadline):
                    await asyncio.sleep(0.005)
                assert _statuses(store, gate_id) == [_PARK_STATUS], (
                    "gate item never parked")
                release_fast.set()
                results = await rt.wait_for_completions()
                return factory, store, fast_id, gate_id, results
            finally:
                await rt.stop()

        factory, store, fast_id, gate_id, results = asyncio.run(main())
        by_agent = {r["agent_id"]: r for r in results}

        fast, gate = by_agent[fast_id], by_agent[gate_id]
        assert fast["status"] is AgentStatus.COMPLETED
        assert gate["status"] is AgentStatus.COMPLETED
        assert fast["attempts"] == 1 and gate["attempts"] == 1
        # The fast path produced zero gate behavior...
        assert _statuses(store, fast_id) == ["COMPLETED"]
        # ...while the gate item parked and resumed its own attempt.
        assert _statuses(store, gate_id) == [_PARK_STATUS, "COMPLETED"]
        # Fast item completed while the gate item was still parked, and
        # the park never stalled its sibling (independence both ways).
        order = store.order
        park_at = order.index((_PARK_RESULT_LABEL, str(gate_id)))
        fast_done = order.index(("result:COMPLETED", str(fast_id)))
        gate_done = order.index(("result:COMPLETED", str(gate_id)))
        assert park_at < fast_done < gate_done
        # Both items kept exactly one attempt persona each.
        assert factory.created_attempts == [1, 1]
        assert factory.tracker["setup_calls"] == 1 + 21