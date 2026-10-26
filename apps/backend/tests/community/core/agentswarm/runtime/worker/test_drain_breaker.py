"""Graceful drain (task 12.2) and upstream circuit breaker (task 12.5)
tests: the worker drains within its grace window; over-grace slices are
released to a reclaimable dispatch state through the existing D34 slice_lost
cooperative path, never cancelled mid-persist. The breaker trips OPEN on
consecutive upstream failures, expires to a probe window, and while OPEN
the runtime fails fast without constructing a subagent. Real _TestDB for
the end-to-end drain runs, hand-fakes only — no MagicMock.
"""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, AgentSwarmDrainConfig, AgentSwarmOrchestrationConfig,
)
from agentclaw.community.core.agentswarm.runtime.breaker import (
    BreakerState, UpstreamBreaker,
)
from agentclaw.community.core.agentswarm.runtime.worker import AgentSwarmWorker
from agentclaw.community.core.agentswarm.runtime.pool import (
    SubagentRuntime,
)

from tests.community.core.agentswarm.runtime.handler.test_swarm_slice import (
    TENANT, FakeFactory, RepeatingLLMProvider, _TestDB, _build_env,
    _finish_decision, _mk_run, _spawn_decision, _wait_decision, _spin_ref,
)


# ---------------------------------------------------------------------------
# Breaker unit tests (pure state machine; timestamps passed in).
# ---------------------------------------------------------------------------

class TestUpstreamBreaker:

    def test_closed_allows_and_threshold_trips_open(self):
        breaker = UpstreamBreaker(failure_threshold=3, open_seconds=60.0)

        assert breaker.state is BreakerState.CLOSED
        assert breaker.allow(100.0) is True

        breaker.record_failure(100.0)
        breaker.record_failure(101.0)
        assert breaker.state is BreakerState.CLOSED

        breaker.record_failure(102.0)
        assert breaker.state is BreakerState.OPEN
        assert breaker.consecutive_failures == 3
        assert breaker.allow(102.5) is False

    def test_open_expires_to_probe_and_success_closes(self):
        breaker = UpstreamBreaker(failure_threshold=1, open_seconds=60.0)
        breaker.record_failure(100.0)
        assert breaker.state is BreakerState.OPEN

        assert breaker.allow(159.9) is False
        assert breaker.allow(160.0) is True

        breaker.record_success()
        assert breaker.state is BreakerState.CLOSED
        assert breaker.consecutive_failures == 0
        assert breaker.allow(160.1) is True

    def test_probe_failure_reopens_at_new_window(self):
        breaker = UpstreamBreaker(failure_threshold=2, open_seconds=60.0)
        breaker.record_failure(100.0)
        breaker.record_failure(100.5)
        assert breaker.state is BreakerState.OPEN

        assert breaker.allow(161.0) is True      # probe window
        breaker.record_failure(161.0)            # probe fails: re-open now

        assert breaker.state is BreakerState.OPEN
        assert breaker.allow(220.9) is False     # window restarted at probe
        assert breaker.allow(221.0) is True

    def test_allow_never_mutates_state(self):
        breaker = UpstreamBreaker(failure_threshold=1, open_seconds=60.0)
        breaker.record_failure(100.0)

        for _ in range(3):
            assert breaker.allow(200.0) is True

        assert breaker.state is BreakerState.OPEN
        assert breaker.consecutive_failures == 1


# ---------------------------------------------------------------------------
# Breaker + runtime integration (12.5). max_width=1 keeps pool processing
# serial so the failure order — and the trip point — is deterministic.
# ---------------------------------------------------------------------------

class _RaisingSubagent:

    def __init__(self, name: str) -> None:
        self.name = name

    async def setup(self) -> None:
        return None

    async def teardown(self) -> None:
        return None

    async def halt(self) -> None:
        return None

    async def run_task(self, envelope) -> dm.SubagentResult:
        raise RuntimeError("llm down")


class _FlakySubagent:
    """Fails the first call, succeeds afterwards (fresh context per attempt)."""

    def __init__(self, name: str, calls: dict[str, int]) -> None:
        self.name = name
        self._calls = calls

    async def setup(self) -> None:
        return None

    async def teardown(self) -> None:
        return None

    async def halt(self) -> None:
        return None

    async def run_task(self, envelope) -> dm.SubagentResult:
        self._calls[self.name] = self._calls.get(self.name, 0) + 1
        if self._calls[self.name] == 1:
            raise RuntimeError("transient upstream error")
        return dm.SubagentResult(output={"ok": True})


class _CountingFactory:
    """Counts creates per agent name — the fail-fast assertion hinges on it."""

    def __init__(self, make) -> None:
        self.creates: dict[str, int] = {}
        self._make = make

    def create(self, spec: dm.AgentSpec, run: dm.Run,
                 agent_id: dm.AgentId | None = None,
                 attempt: int = 1):
        self.creates[spec.name] = self.creates.get(spec.name, 0) + 1
        return self._make(spec.name)


def _flat_run(tenant=TENANT) -> dm.Run:
    return dm.Run(
        run_id=dm.RunId(uuid.uuid4().hex),
        tenant=dm.TenantId(tenant),
        goal="drain breaker goal",
        agent_catalog=tuple(
            dm.AgentSpec(name=n, role="research") for n in ("a1", "a2", "a3")
        ),
        provider=dm.ProviderKind("stub"),
        request_metadata=dm.RequestMetadata(),
        status=dm.RunStatus.RUNNING,
        started_at=dm.EpochMillis(int(time.time() * 1000)),
    )


def _env_for(name: str) -> dm.TaskEnvelope:
    return dm.TaskEnvelope(
        name=name, objective=f"goal-{name}", output_schema={},
        boundaries="", parent_goal="drain breaker goal",
    )


class TestBreakerRuntimeIntegration:

    def test_open_breaker_fails_fast_without_constructing_subagent(self):
        async def main():
            factory = _CountingFactory(_RaisingSubagent)
            breaker = UpstreamBreaker(failure_threshold=2, open_seconds=60.0)
            rt = SubagentRuntime(
                factory=factory, max_width=1, max_attempts=1,
                upstream_breaker=breaker,
            )
            run = _flat_run()
            # Spawn after start(): max_width=1 bounds the queue too, and an
            # unconsumed put would block the second spawn.
            await rt.start()
            agent_ids = [
                await rt.spawn(run, _env_for(name))
                for name in ("a1", "a2", "a3")
            ]
            try:
                completions = await rt.wait_for_completions(timeout=5.0)
            finally:
                await rt.stop()
            return run, agent_ids, completions, factory, breaker

        run, agent_ids, completions, factory, breaker = asyncio.run(main())

        by_agent = {c["agent_id"]: c for c in completions}
        assert len(completions) == 3

        first = by_agent[agent_ids[0]]
        second = by_agent[agent_ids[1]]
        third = by_agent[agent_ids[2]]
        assert first["status"] is dm.AgentStatus.FAILED
        assert first["error"] == "llm down"
        assert second["status"] is dm.AgentStatus.FAILED
        assert second["error"] == "llm down"
        # The OPEN breaker fails the third execution before any subagent is
        # constructed — no attempt cycle against the dead upstream.
        assert third["status"] is dm.AgentStatus.FAILED
        assert third["error"] == "upstream_breaker_open"
        assert third["attempts"] == 1

        assert breaker.state is BreakerState.OPEN
        assert factory.creates.get("a1") == 1
        assert factory.creates.get("a2") == 1
        assert "a3" not in factory.creates

    def test_probe_success_closes_breaker_through_runtime(self):
        async def main():
            calls: dict[str, int] = {}
            factory = _CountingFactory(lambda name: _FlakySubagent(name, calls))
            breaker = UpstreamBreaker(failure_threshold=1, open_seconds=0.0)
            rt = SubagentRuntime(
                factory=factory, max_width=1, max_attempts=2,
                upstream_breaker=breaker,
            )
            run = _flat_run()
            agent_id = await rt.spawn(run, _env_for("a1"))
            await rt.start()
            try:
                completions = await rt.wait_for_completions(timeout=5.0)
            finally:
                await rt.stop()
            return agent_id, completions, factory, breaker

        agent_id, completions, factory, breaker = asyncio.run(main())

        # Attempt 1 trips OPEN; attempt 2 is the probe and succeeds, which
        # closes the breaker through the runtime's success recording.
        assert len(completions) == 1
        result = completions[0]
        assert result["agent_id"] == agent_id
        assert result["status"] is dm.AgentStatus.COMPLETED
        assert result["attempts"] == 2
        assert result["error"] is None
        assert breaker.state is BreakerState.CLOSED
        assert factory.creates == {"a1": 2}

    def test_disabled_by_default_keeps_existing_ctor_surface(self):
        async def main():
            factory = _CountingFactory(_RaisingSubagent)
            rt = SubagentRuntime(factory=factory, max_width=1, max_attempts=1)
            run = _flat_run()
            agent_id = await rt.spawn(run, _env_for("a1"))
            await rt.start()
            try:
                completions = await rt.wait_for_completions(timeout=5.0)
            finally:
                await rt.stop()
            return agent_id, completions, factory

        agent_id, completions, factory = asyncio.run(main())

        assert len(completions) == 1
        assert completions[0]["agent_id"] == agent_id
        assert completions[0]["status"] is dm.AgentStatus.FAILED
        assert completions[0]["error"] == "llm down"
        assert factory.creates == {"a1": 1}


# ---------------------------------------------------------------------------
# Drain (12.2) against the worker: deterministic stub handler state machine.
# ---------------------------------------------------------------------------

def _stub_dispatch(run_id="drain-run-1", epoch=1) -> dict:
    return {
        "run_id": run_id, "tenant": TENANT, "claim_epoch": epoch,
        "reclaims": 0, "cancel_requested": False,
    }


class DrainDispatchRepo:
    """One-shot claim; records leases, failures, reschedules, claim ticks."""

    def __init__(self, pending: int = 0) -> None:
        self._dispatches: list[dict] = []
        self.leases: list = []
        self.fails: list = []
        self.reschedules: list = []
        self.claim_ticks = 0
        self.pending = pending

    def enqueue_dispatch(self, dispatch: dict) -> None:
        self._dispatches.append(dispatch)

    def count_pending_dispatches(self) -> int:
        return self.pending

    def reclaim_batch(self, worker_id, limit, lease_seconds):
        return []

    def claim_batch(self, worker_id, tenant_filter, limit, lease_seconds):
        self.claim_ticks += 1
        claimed, self._dispatches = self._dispatches[:limit], []
        return claimed

    def renew_lease(self, run_id, tenant, worker_id, lease_seconds, claim_epoch):
        self.leases.append((run_id, claim_epoch))
        return True

    def reschedule(self, run_id, tenant, worker_id, delay_seconds, error, claim_epoch):
        self.reschedules.append((run_id, error, claim_epoch))
        return True

    def fail(self, run_id, tenant, error, worker_id, claim_epoch):
        self.fails.append((run_id, error, claim_epoch))
        return True


class DrainHandler:
    """SwarmRunHandler stand-in: a slice parks in handle_slice until its
    gate opens (normal completion) or set_slice_lost is signalled (the D34
    cooperative abort the drain uses), which wakes the parked slice to
    return 'retry'.
    """

    def __init__(self) -> None:
        self.calls: list = []
        self.outcomes: dict = {}
        self.gates: dict = {}
        self.lost_runs: set = set()

    def gate(self, run_id) -> asyncio.Event:
        return self.gates.setdefault(run_id, asyncio.Event())

    def set_slice_lost(self, lost: bool, run_id=None) -> None:
        self.calls.append(("set-slice-lost", lost, run_id))
        if lost:
            self.lost_runs.add(run_id)
            self.gate(run_id).set()

    async def handle_slice(self, run_id, tenant, claim_epoch,
                           cancel_requested=False, *, worker_id=None):
        self.calls.append(("handle-slice-begin", run_id))
        await self.gate(run_id).wait()
        self.calls.append(("handle-slice-end", run_id))
        outcome = "retry" if run_id in self.lost_runs else "complete"
        self.outcomes[run_id] = outcome
        return outcome

    def force_stuck(self, run_id, tenant, epoch, error, worker_id=None):
        self.calls.append(("force-stuck", run_id))
        return True

    def force_fail(self, run_id, tenant, epoch, error, worker_id=None):
        self.calls.append(("force-fail", run_id))
        return True


def _drain_config(grace: float, *, lease: float = 1.0,
                  poll: float = 0.02) -> AgentSwarmConfig:
    orch = AgentSwarmOrchestrationConfig(
        poll_interval=poll, poll_jitter=0.0, lease_seconds=lease,
        batch_size=4, slice_max_seconds=10.0,
    )
    return AgentSwarmConfig(
        orchestration=orch,
        drain=AgentSwarmDrainConfig(grace_seconds=grace),
    )


class TestWorkerDrain:

    def test_within_grace_completes_without_slice_lost(self):
        async def main():
            dispatch = _stub_dispatch()
            repo = DrainDispatchRepo(pending=3)
            repo.enqueue_dispatch(dispatch)
            handler = DrainHandler()
            worker = AgentSwarmWorker(
                config=_drain_config(grace=2.0, lease=0.06),
                handler=handler, dispatch_repo=repo,
            )
            await worker.startup()
            await asyncio.sleep(0.05)   # slice is parked in-flight

            async def normal_completion():
                await asyncio.sleep(0.1)
                handler.gate("drain-run-1").set()

            asyncio.create_task(normal_completion())
            start = time.monotonic()
            await worker.shutdown()
            elapsed = time.monotonic() - start
            return worker, handler, repo, elapsed, handler.calls

        worker, handler, repo, elapsed, calls = asyncio.run(main())

        # Drained inside the grace window, with no cooperative abort.
        assert elapsed < 2.0
        assert [c for c in calls if c[0] == "set-slice-lost"] == []
        assert handler.outcomes["drain-run-1"] == "complete"
        assert worker._task.done() and not worker._task.cancelled()
        assert worker._heartbeat_task.done()
        assert repo.fails == []

    def test_over_grace_signals_slice_lost_and_unwinds_cleanly(self):
        async def main():
            repo = DrainDispatchRepo()
            repo.enqueue_dispatch(_stub_dispatch())
            handler = DrainHandler()
            worker = AgentSwarmWorker(
                config=_drain_config(grace=0.05),
                handler=handler, dispatch_repo=repo,
            )
            await worker.startup()
            await asyncio.sleep(0.05)   # slice parked; grace cannot cover it

            start = time.monotonic()
            await worker.shutdown()
            elapsed = time.monotonic() - start
            return worker, handler, repo, elapsed

        worker, handler, repo, elapsed = asyncio.run(main())

        # Returned after ~grace plus the short abort join — long before the
        # parked slice would have sat out its own budget.
        assert elapsed < 2.0
        assert ("set-slice-lost", True, "drain-run-1") in handler.calls
        assert handler.outcomes["drain-run-1"] == "retry"
        # Cooperative unwind: the loop task finished normally, nothing was
        # cancelled mid-slice and no dispatch was dropped on the floor.
        assert worker._task.done() and not worker._task.cancelled()
        assert repo.fails == []
        assert repo.claim_ticks == 1

    def test_heartbeats_renew_while_draining(self):
        async def main():
            repo = DrainDispatchRepo()
            repo.enqueue_dispatch(_stub_dispatch())
            handler = DrainHandler()
            worker = AgentSwarmWorker(
                config=_drain_config(grace=30.0, lease=0.06),
                handler=handler, dispatch_repo=repo,
            )
            await worker.startup()
            await asyncio.sleep(0.05)
            # Drain phase: claiming stopped, the slice is still in flight.
            worker._running = False
            leases_before = len(repo.leases)
            await asyncio.sleep(0.09)   # several heartbeat intervals
            renewed = len(repo.leases) - leases_before
            handler.gate("drain-run-1").set()      # let it finish
            await worker.shutdown()
            return renewed

        renewed = asyncio.run(main())

        # The heartbeat outlives _running while slices are in flight, so a
        # healthy drain does not feed its own leases to the reclaimer.
        assert renewed >= 1

    def test_shutdown_without_started_worker_is_noop(self):
        worker = AgentSwarmWorker(
            config=_drain_config(grace=0.01),
            handler=DrainHandler(), dispatch_repo=DrainDispatchRepo(),
        )

        asyncio.run(worker.shutdown())

        assert worker._running is False
        assert worker._task is None


# ---------------------------------------------------------------------------
# Drain through the REAL SwarmRunHandler + durable dispatch rows: over-grace
# slices release to a reclaimable READY dispatch; within-grace slices finish
# and complete the run. StaticPool SQLite because the worker drives repos
# from asyncio.to_thread.
# ---------------------------------------------------------------------------

@pytest.fixture()
def real_db():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
    yield _TestDB(engine)
    engine.dispose()


def _real_drain_config(grace: float) -> AgentSwarmConfig:
    orch = AgentSwarmOrchestrationConfig(
        poll_interval=0.02, poll_jitter=0.0, lease_seconds=60,
        slice_max_seconds=10.0, slice_interval=0.0,
        orchestrator_turn_interval=0.01, batch_size=4,
    )
    return AgentSwarmConfig(
        orchestration=orch,
        drain=AgentSwarmDrainConfig(grace_seconds=grace),
    )


def _job_state(db, run_id):
    with db.orm_session() as session:
        row = session.execute(
            select(om.AcAgentSwarmRunDispatchModel).where(
                om.AcAgentSwarmRunDispatchModel.run_id == run_id,
            )
        ).scalar_one()
        return {
            "status": row.status,
            "claim_epoch": row.claim_epoch,
            "last_error": row.last_error,
        }


class TestRealHandlerDrain:

    def test_over_grace_slice_released_to_reclaimable_pending(self, real_db):
        # A wait on a permanently-unsatisfied ref keeps the real slice
        # non-terminal for its whole 10s budget, so grace cannot cover it.
        # Grace 0.4 keeps the abort join (min(grace, 5s)) above the wait
        # loop's 0.25s lost-check chunk, so the unwind stays cooperative.
        env = _build_env(
            real_db, [], FakeFactory(),
            config=_real_drain_config(grace=0.4),
            llm=RepeatingLLMProvider(_wait_decision("ghost")),
        )
        run = _mk_run()
        _spin_ref(env, run)
        env.run_repo.create(run)
        env.dispatch_repo.enqueue(run_id=run.run_id, tenant=TENANT, deadline_seconds=3600)

        async def main():
            worker = AgentSwarmWorker(
                config=env.cfg, handler=env.handler, dispatch_repo=env.dispatch_repo,
            )
            await worker.startup()
            await asyncio.sleep(0.08)   # the real slice is mid-loop
            start = time.monotonic()
            await worker.shutdown()
            return time.monotonic() - start

        elapsed = asyncio.run(main())

        # Shutdown returned near grace, not after the slice's 10s budget.
        assert elapsed < 3.0
        stored_run = env.run_repo.get(run.run_id, TENANT)
        state = _job_state(real_db, run.run_id)
        # Released through the slice-lost path: dispatch READY again, another
        # worker can immediately reclaim it with epoch fencing — the run
        # was never cancelled mid-write.
        assert stored_run.status is dm.RunStatus.RUNNING
        assert state["status"] == "READY"
        assert state["last_error"] == "slice lost"
        reclaim = env.dispatch_repo.claim_batch("worker-2", None, 1, 60)
        assert [j["run_id"] for j in reclaim] == [run.run_id]
        assert reclaim[0]["claim_epoch"] == state["claim_epoch"] + 1

    def test_within_grace_real_slice_completes(self, real_db):
        gate = asyncio.Event()
        env = _build_env(
            real_db,
            [
                _spawn_decision("a1", "goal-a1"),
                _wait_decision("a1"),
                _finish_decision("drained"),
            ],
            FakeFactory(scripts={"a1": [{"n": 1}]}, gate=gate),
            config=_real_drain_config(grace=3.0),
        )
        run = _mk_run()
        env.run_repo.create(run)
        env.dispatch_repo.enqueue(run_id=run.run_id, tenant=TENANT, deadline_seconds=3600)

        async def main():
            worker = AgentSwarmWorker(
                config=env.cfg, handler=env.handler, dispatch_repo=env.dispatch_repo,
            )
            await worker.startup()

            # A fixed 30ms sleep races slow CI workers that have not claimed
            # the dispatch yet. Wait until the slice is genuinely in flight,
            # then release it shortly after drain starts so the real handler
            # completes inside its 3s grace window.
            deadline = time.monotonic() + 3.0
            while not worker._inflight_snapshot():
                if time.monotonic() > deadline:
                    await worker.shutdown()
                    raise AssertionError("worker did not claim the run in time")
                await asyncio.sleep(0.01)

            async def release_slice():
                await asyncio.sleep(0.1)
                gate.set()

            release = asyncio.create_task(release_slice())
            try:
                await worker.shutdown()
            finally:
                release.cancel()
                await asyncio.gather(release, return_exceptions=True)

        asyncio.run(main())

        stored_run = env.run_repo.get(run.run_id, TENANT)
        state = _job_state(real_db, run.run_id)
        assert stored_run.status is dm.RunStatus.COMPLETED
        assert stored_run.final_answer == "drained"
        assert state["status"] == "DONE"
        assert state["last_error"] is None

class TestHeartbeatDuringDrain:
    """Drain edge (12.2): between batch jobs the in-flight map is
    momentarily empty while the poll loop still holds claimed, un-started
    jobs. The heartbeat must stay alive for those gaps — exiting would
    strand the drain lifecycle. Actual lease renewal is the dedicated
    thread's job (test_lease_renewal.py); here the heartbeat only has to
    outlive the gap.
    """

    def test_heartbeat_survives_empty_inflight_gap(self):
        async def main():
            repo = DrainDispatchRepo()
            handler = DrainHandler()
            worker = AgentSwarmWorker(
                config=_drain_config(grace=30.0, lease=0.06),
                handler=handler, dispatch_repo=repo,
            )
            # Drain under way: claiming stopped, no slice in flight this
            # instant, but the poll task is alive with claimed jobs queued.
            worker._running = False
            worker._task = asyncio.create_task(asyncio.sleep(0.2))
            heartbeat = asyncio.create_task(worker._heartbeat_loop())
            try:
                await asyncio.sleep(0.08)   # several heartbeat intervals
                # Old condition (running or inflight) exits here; a heartbeat
                # exiting here would strand the drain lifecycle.
                assert not heartbeat.done()

                # poll loop finished and nothing is in flight: clean exit
                await asyncio.wait_for(heartbeat, timeout=1.0)
                assert heartbeat.done() and not heartbeat.cancelled()
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

        asyncio.run(main())
