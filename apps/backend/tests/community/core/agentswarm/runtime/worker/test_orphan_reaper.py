"""Maintenance reaper tests (async-run-and-resume 4.1/4.2): the worker's
third task pages RUNNING runs, probes job claimability consistently with
the claim predicates, re-enqueues queue orphans behind the uk_job_run
convergence point, and forces a fenced terminal TIMED_OUT for orphans
past their wall-clock deadline — emitting one stuck/orphan guard event
per acted run. Real _TestDB (StaticPool: the sweep drives repositories
via runtime.offload.run_blocking), hand-fakes only — no MagicMock.
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
    AgentSwarmConfig, AgentSwarmDrainConfig, AgentSwarmMaintenanceConfig,
    AgentSwarmOrchestrationConfig,
)
from agentclaw.community.core.agentswarm.runtime.worker import AgentSwarmWorker

from tests.community.core.agentswarm.runtime.handler.test_swarm_slice import (
    TENANT, FakeFactory, RepeatingLLMProvider, _TestDB, _build_env,
    _finish_decision, _mk_run,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
    yield _TestDB(engine)
    engine.dispose()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _reaper_config(**orch_overrides) -> AgentSwarmConfig:
    # lease_seconds=1 shrinks the submit-grace window to 1s; reaper
    # fixtures age runs past it declaratively via started_at.
    orch = AgentSwarmOrchestrationConfig(
        slice_max_seconds=0.2,
        slice_interval=0.0,
        orchestrator_turn_interval=0.01,
        lease_seconds=1,
    )
    if orch_overrides:
        import dataclasses
        orch = dataclasses.replace(orch, **orch_overrides)
    return AgentSwarmConfig(orchestration=orch, message_retention_tail=50)


def _aged_run(age_ms: int, **kwargs) -> dm.Run:
    run = _mk_run(**kwargs)
    run.started_at = dm.EpochMillis(_now_ms() - age_ms)
    if run.active_idempotency_key is None:
        run.active_idempotency_key = dm.IdempotencyKey(uuid.uuid4().hex)
    return run


def _mk_worker(env, config: AgentSwarmConfig | None = None) -> AgentSwarmWorker:
    return AgentSwarmWorker(
        config=config or env.cfg, handler=env.handler, job_repo=env.job_repo,
        run_repo=env.run_repo, guard_repo=env.guard_repo,
    )


def _job_rows(db, run_id) -> list:
    with db.orm_session() as session:
        rows = session.execute(
            select(om.AcAgentSwarmJobModel).where(
                om.AcAgentSwarmJobModel.run_id == run_id,
            )
        ).scalars().all()
        return [
            {"status": r.status, "claim_epoch": r.claim_epoch,
             "reclaims": r.reclaims, "progress_marker": r.progress_marker}
            for r in rows
        ]


def _stuck_orphan_events(env, run) -> list[dm.GuardEvent]:
    return [
        e for e in env.guard_repo.list_by_run(run.run_id, run.tenant)
        if e.kind is dm.GuardKind.STUCK
    ]


class _FlakyProbeJobRepo:
    """Hand-fake wrapper: get_run_job always raises for one target run."""

    def __init__(self, inner, failing_run_id) -> None:
        self._inner = inner
        self._failing_run_id = str(failing_run_id)

    def get_run_job(self, run_id, tenant):
        if str(run_id) == self._failing_run_id:
            raise RuntimeError("db down mid-probe")
        return self._inner.get_run_job(run_id, tenant)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class TestMaintenanceLoopLifecycle:

    def test_third_task_starts_and_cancels_cleanly_in_shutdown(self, db):
        async def main():
            env = _build_env(db, [_finish_decision("x")], FakeFactory(),
                             config=_reaper_config())
            worker = _mk_worker(env)
            await worker.startup()
            try:
                assert worker._maintenance_task is not None
                assert not worker._maintenance_task.done()
            finally:
                await worker.shutdown()
            assert worker._maintenance_task.done()
            assert worker._maintenance_task.cancelled()

        asyncio.run(main())

    def test_disabled_knob_starts_no_task(self, db):
        async def main():
            env = _build_env(db, [_finish_decision("x")], FakeFactory(),
                             config=_reaper_config())
            config = AgentSwarmConfig(
                orchestration=_reaper_config().orchestration,
                maintenance=AgentSwarmMaintenanceConfig(enabled=False),
            )
            worker = _mk_worker(env, config)
            await worker.startup()
            assert worker._maintenance_task is None
            await worker.shutdown()

        asyncio.run(main())

    def test_worker_without_repo_deps_hosts_no_task(self, db):
        async def main():
            env = _build_env(db, [_finish_decision("x")], FakeFactory(),
                             config=_reaper_config())
            worker = AgentSwarmWorker(
                config=env.cfg, handler=env.handler, job_repo=env.job_repo,
            )
            await worker.startup()
            assert worker._maintenance_task is None
            await worker.shutdown()

        asyncio.run(main())

    def test_reaper_sweeps_on_its_own_cadence(self, db):
        env = _build_env(
            db, [], FakeFactory(), config=_reaper_config(),
            llm=RepeatingLLMProvider(
                "{\"kind\": \"wait\", \"refs\": [\"ghost\"], \"mode\": \"all\"}"),
        )
        run = _aged_run(2_000)
        env.run_repo.create(run)   # queue orphan: RUNNING run, no job row

        async def main():
            import dataclasses
            orch = dataclasses.replace(
                # poll parked far away: only the maintenance path may act
                # inside the wait window, so the job row stays PENDING.
                _reaper_config().orchestration,
                poll_interval=5.0, poll_jitter=0.0,
            )
            config = AgentSwarmConfig(
                orchestration=orch,
                drain=AgentSwarmDrainConfig(grace_seconds=0.05),
                maintenance=AgentSwarmMaintenanceConfig(
                    reaper_interval_seconds=0.02),
            )
            worker = _mk_worker(env, config)
            await worker.startup()
            await asyncio.sleep(0.3)
            await worker.shutdown()

        asyncio.run(main())

        assert len(_job_rows(db, run.run_id)) == 1


class TestQueueOrphanReenqueue:

    def test_orphan_without_job_row_is_reenqueued_with_guard_event(self, db):
        env = _build_env(db, [_finish_decision("x")], FakeFactory(),
                         config=_reaper_config())
        run = _aged_run(2_000)
        env.run_repo.create(run)
        worker = _mk_worker(env)

        acted = asyncio.run(worker._sweep_once())

        assert acted == 1
        rows = _job_rows(db, run.run_id)
        assert len(rows) == 1
        assert rows[0]["status"] == "PENDING"
        assert rows[0]["claim_epoch"] == 0
        events = _stuck_orphan_events(env, run)
        assert [e.details for e in events] == [{"reason": "orphan"}]
        # The revived row is a normally claimable job.
        claimed = env.job_repo.claim_batch("reclaimer", None, 1, 60)
        assert [c["run_id"] for c in claimed] == [run.run_id]

    def test_terminal_job_under_running_run_is_revived(self, db):
        env = _build_env(db, [_finish_decision("x")], FakeFactory(),
                         config=_reaper_config())
        run = _aged_run(2_000)
        env.run_repo.create(run)
        env.job_repo.enqueue(run_id=run.run_id, tenant=TENANT,
                             deadline_seconds=3600)
        claim = env.job_repo.claim_batch("doomed", None, 1, 60)[0]
        assert env.job_repo.complete(run.run_id, run.tenant, "doomed",
                                     claim["claim_epoch"])

        acted = asyncio.run(_mk_worker(env)._sweep_once())

        assert acted == 1
        rows = _job_rows(db, run.run_id)
        assert len(rows) == 1
        assert rows[0]["status"] == "PENDING"
        # Revival bumped the epoch: pre-terminal-era fenced writes can
        # never land against the revived row.
        assert rows[0]["claim_epoch"] == claim["claim_epoch"] + 1
        assert rows[0]["reclaims"] == 0
        assert rows[0]["progress_marker"] == -1
        assert _stuck_orphan_events(env, run)

    def test_reachable_job_rows_are_left_alone(self, db):
        env = _build_env(db, [_finish_decision("x")], FakeFactory(),
                         config=_reaper_config())
        run = _aged_run(2_000)
        env.run_repo.create(run)
        env.job_repo.enqueue(run_id=run.run_id, tenant=TENANT,
                             deadline_seconds=3600)

        acted = asyncio.run(_mk_worker(env)._sweep_once())

        assert acted == 0
        # PENDING (run_at future-proofed by the claim predicates) stays
        # untouched; no guard event fired.
        assert _job_rows(db, run.run_id) == [{
            "status": "PENDING", "claim_epoch": 0, "reclaims": 0,
            "progress_marker": -1,
        }]
        assert _stuck_orphan_events(env, run) == []

    def test_running_row_with_live_lease_is_left_alone(self, db):
        env = _build_env(db, [_finish_decision("x")], FakeFactory(),
                         config=_reaper_config())
        run = _aged_run(2_000)
        env.run_repo.create(run)
        env.job_repo.enqueue(run_id=run.run_id, tenant=TENANT,
                             deadline_seconds=3600)
        env.job_repo.claim_batch("owner", None, 1, 60)

        acted = asyncio.run(_mk_worker(env)._sweep_once())

        assert acted == 0
        assert _job_rows(db, run.run_id)[0]["status"] == "RUNNING"
        assert _stuck_orphan_events(env, run) == []

    def test_fresh_run_inside_submit_grace_is_skipped(self, db):
        env = _build_env(db, [_finish_decision("x")], FakeFactory(),
                         config=_reaper_config())
        run = _aged_run(0)   # inside the one-lease-window submit grace
        env.run_repo.create(run)   # no job row yet — mid-submit shape

        acted = asyncio.run(_mk_worker(env)._sweep_once())

        assert acted == 0
        assert _job_rows(db, run.run_id) == []
        assert _stuck_orphan_events(env, run) == []

    def test_concurrent_sweeps_converge_on_one_job_row(self, db):
        env = _build_env(db, [_finish_decision("x")], FakeFactory(),
                         config=_reaper_config())
        run = _aged_run(2_000)
        env.run_repo.create(run)
        worker_a = _mk_worker(env)
        worker_b = _mk_worker(env)

        async def main():
            # Sequential is the honest staging on the shared StaticPool
            # connection: the convergence under proof is entirely DB-side
            # (uk_job_run collision + the loser's CAS refusal), which the
            # second INSERT hits identically after the first commits.
            return await worker_a._requeue_orphan(run), await worker_b._requeue_orphan(run)

        acted_a, acted_b = asyncio.run(main())

        # uk_job_run is the convergence point: exactly one reaper's
        # INSERT won; the loser's collision-CAS matched a live PENDING
        # row (never terminal) and refused to touch it.
        assert acted_a + acted_b == 1
        assert len(_job_rows(db, run.run_id)) == 1
        assert len(_stuck_orphan_events(env, run)) == 1

    def test_one_bad_run_does_not_wedge_the_sweep(self, db):
        env = _build_env(db, [_finish_decision("x")], FakeFactory(),
                         config=_reaper_config())
        probed = _aged_run(2_000)
        env.run_repo.create(probed)          # faults first: probe raises
        healthy = _aged_run(2_000)
        env.run_repo.create(healthy)
        worker = _mk_worker(env)
        worker._job_repo = _FlakyProbeJobRepo(env.job_repo,
                                              failing_run_id=probed.run_id)

        acted = asyncio.run(worker._sweep_once())

        assert acted == 1
        assert len(_job_rows(db, probed.run_id)) == 0
        assert _job_rows(db, healthy.run_id)[0]["status"] == "PENDING"


class TestDeadlineExpiryLeg:

    def _expired_orphan_env(self, db, *, with_job_row: bool):
        env = _build_env(
            db, [], FakeFactory(),
            config=_reaper_config(wall_clock_seconds=0.05),
            llm=RepeatingLLMProvider(
                "{\"kind\": \"wait\", \"refs\": [\"ghost\"], \"mode\": \"all\"}"),
        )
        run = _aged_run(60_000)   # far past the 0.05s wall clock
        env.run_repo.create(run)
        if with_job_row:
            env.job_repo.enqueue(run_id=run.run_id, tenant=TENANT,
                                 deadline_seconds=3600)
            claim = env.job_repo.claim_batch("doomed", None, 1, 60)[0]
            assert env.job_repo.complete(run.run_id, run.tenant, "doomed",
                                         claim["claim_epoch"])
        return env, run

    def test_past_deadline_orphan_goes_fenced_timed_out_releasing_slot(self, db):
        env, run = self._expired_orphan_env(db, with_job_row=False)
        assert env.run_repo.count_active_runs(TENANT) == 1
        worker = _mk_worker(env)

        acted = asyncio.run(worker._sweep_once())

        assert acted == 1
        stored = env.run_repo.get(run.run_id, run.tenant)
        assert stored.status is dm.RunStatus.TIMED_OUT
        assert stored.termination_reason is dm.TerminationReason.WALL_CLOCK
        assert "orphan reaper" in stored.error
        assert stored.ended_at is not None
        # 4.2's contract: the tenant admission slot and the active
        # idempotency key are released by the terminal-write handling.
        assert env.run_repo.count_active_runs(TENANT) == 0
        assert env.run_repo.get_by_idempotency(
            run.active_idempotency_key, run.tenant) is None
        assert stored.active_idempotency_key is None
        assert _stuck_orphan_events(env, run)
        # The reaper provisioned its own fence: exactly one job row.
        rows = _job_rows(db, run.run_id)
        assert len(rows) == 1

    def test_past_deadline_claim_after_forced_run_terminates_via_fast_path(self, db):
        # The fence-provisioning row stays PENDING after the force; a
        # claim must never resurrect the terminal run — the slice's
        # already-terminal fast path completes the job instead.
        env, run = self._expired_orphan_env(db, with_job_row=False)
        worker = _mk_worker(env)
        assert asyncio.run(worker._sweep_once()) == 1

        claimed = env.job_repo.claim_batch("late", None, 1, 60)
        assert len(claimed) == 1
        outcome = asyncio.run(env.handler.handle_slice(
            run.run_id, run.tenant, claimed[0]["claim_epoch"],
            worker_id="late",
        ))

        assert outcome == "complete"
        assert env.run_repo.get(run.run_id, run.tenant).status \
            is dm.RunStatus.TIMED_OUT
        assert _job_rows(db, run.run_id)[0]["status"] == "SUCCEEDED"

    def test_past_deadline_orphan_with_dead_job_row_fences_at_its_epoch(self, db):
        env, run = self._expired_orphan_env(db, with_job_row=True)
        worker = _mk_worker(env)

        acted = asyncio.run(worker._sweep_once())

        assert acted == 1
        stored = env.run_repo.get(run.run_id, run.tenant)
        assert stored.status is dm.RunStatus.TIMED_OUT
        assert stored.termination_reason is dm.TerminationReason.WALL_CLOCK
        # The dead row supplied the fence and keeps its terminal shape.
        assert _job_rows(db, run.run_id) == [{
            "status": "SUCCEEDED", "claim_epoch": 1, "reclaims": 0,
            "progress_marker": 0,
        }]
        assert len(_stuck_orphan_events(env, run)) == 1

    def test_unexpired_orphan_still_takes_the_reenqueue_leg(self, db):
        env = _build_env(db, [_finish_decision("x")], FakeFactory(),
                         config=_reaper_config(wall_clock_seconds=3600.0))
        run = _aged_run(10_000)
        env.run_repo.create(run)

        acted = asyncio.run(_mk_worker(env)._sweep_once())

        assert acted == 1
        assert env.run_repo.get(run.run_id, run.tenant).status \
            is dm.RunStatus.RUNNING
        assert _job_rows(db, run.run_id)[0]["status"] == "PENDING"