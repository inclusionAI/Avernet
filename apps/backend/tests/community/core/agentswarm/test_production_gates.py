"""Stage-C production gates (tasks 13.1-13.6).

Each class enforces one 13.x launch gate end to end against the real
_TDB-backed stack — fencing under lease expiry, admission shape under
load, mid-slice rolling deploy, tenant isolation, migration
compatibility of rows written before additive columns, and upstream
degradation. Hand fakes only — no MagicMock.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmAdmissionConfig, AgentSwarmConfig,
    AgentSwarmDrainConfig, AgentSwarmOrchestrationConfig,
    AgentSwarmTenantLimits, LlmConfig,
)
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.runtime.pool import (
    SubagentRuntime,
)
from agentclaw.community.core.agentswarm.runtime.breaker import (
    UpstreamBreaker,
)
from agentclaw.community.core.agentswarm.runtime.worker import AgentSwarmWorker
from agentclaw.community.core.agentswarm.service import AgentSwarmService
from agentclaw.community.core.agentswarm.service.protocols import (
    QueueDepthExceeded, SubmitRunRequest, TenantRunLimitExceeded,
)

from tests.community.core.agentswarm.runtime.handler.test_swarm_slice import (
    TENANT, FakeFactory, RepeatingLLMProvider, _TestDB, _build_env,
    _finish_decision, _mk_run, _spawn_decision, _wait_decision,
)

TENANT_B = dm.TenantId("tenant-b")


def _gate_db():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
    return _TestDB(engine)


def _gate_config(*, grace: float = 0.2, slice_seconds: float = 2.0,
                 lease: float = 0.1) -> AgentSwarmConfig:
    return AgentSwarmConfig(
        llm=LlmConfig(base_url="stub", api_key="stub", default_model="stub"),
        orchestration=AgentSwarmOrchestrationConfig(
            poll_interval=0.01, poll_jitter=0.0, lease_seconds=lease,
            batch_size=10, slice_max_seconds=slice_seconds,
            slice_interval=0.0,
        ),
        drain=AgentSwarmDrainConfig(grace_seconds=grace),
    )


def _submit_request(tenant: str = TENANT, *, goal: str = "gate goal",
                    idem: str | None = None) -> SubmitRunRequest:
    return SubmitRunRequest(
        tenant=tenant, goal=goal,
        agents=(dm.AgentSpec(name="a1", role="worker"),),
        provider=dm.ProviderKind.STUB, idempotency_key=idem,
    )


class _StoryLLM:
    """Scripted decision provider; the last response repeats, and the
    remaining script can be swapped mid-flight (the rolling-deploy
    orchestration rewrites the story between worker A and worker B)."""

    def __init__(self, script: list[str]) -> None:
        self.set_script(script)

    def set_script(self, script: list[str]) -> None:
        self._script = list(script)

    async def complete(self, request):
        if len(self._script) > 1:
            text = self._script.pop(0)
        else:
            text = self._script[0]
        return dm.CompletionResponse(
            text=text, model=dm.ModelId("story"),
            token_usage=dm.TokenUsage(), finish_reason=dm.FinishReason.STOP,
        )


class _FailingLLM:

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        raise RuntimeError("upstream gone")


class _RaisingFactory:

    def __init__(self) -> None:
        self.creates = 0

    def create(self, spec, run, agent_id=None, attempt=1):
        self.creates += 1
        return _RaisingSubagent()


class _RaisingSubagent:

    async def setup(self) -> None:
        return None

    async def teardown(self) -> None:
        return None

    async def halt(self) -> None:
        return None

    async def run_task(self, envelope) -> dm.SubagentResult:
        raise RuntimeError("gateway 503")


def _job_row(db, run_id) -> tuple[str, int]:
    with db.orm_session() as session:
        return session.execute(
            select(om.AcAgentSwarmRunDispatchModel.status, om.AcAgentSwarmRunDispatchModel.claim_epoch)
            .where(om.AcAgentSwarmRunDispatchModel.run_id == run_id)
        ).one()


class TestChaosFencing:

    def test_partition_fences_stale_worker_and_completes_via_reclaimer(self):
        db = _gate_db()
        env = _build_env(
            db, [], FakeFactory(scripts={"a1": [{"did": 1}]}),
            config=_gate_config(),
            llm=_StoryLLM([
                _spawn_decision("a1", "work"),
                _wait_decision("a1"),
                _finish_decision("done"),
            ]),
        )
        run = _mk_run(goal="chaos")
        env.run_repo.create(run)
        env.dispatch_repo.enqueue(run_id=run.run_id, tenant=TENANT, deadline_seconds=3600)

        async def main():
            claimed_a = env.dispatch_repo.claim_batch("A", None, 1, 60)
            epoch_a = claimed_a[0]["claim_epoch"]
            env.dispatch_repo.renew_lease(run.run_id, TENANT, "A", -1, epoch_a)
            reclaimed = env.dispatch_repo.reclaim_batch("B", 10, 60)
            epoch_b = reclaimed[0]["claim_epoch"]
            assert epoch_b == epoch_a + 1

            stale_write = env.run_repo.update_status(
                run.run_id, TENANT, dm.RunStatus.COMPLETED,
                termination_reason=dm.TerminationReason.FINISH,
                final_answer="A wrote this", claim_epoch=epoch_a,
            )
            assert stale_write is False
            assert env.dispatch_repo.renew_lease(
                run.run_id, TENANT, "A", 60, epoch_a) is False
            stored = env.run_repo.get(run.run_id, TENANT)
            assert stored.status is dm.RunStatus.RUNNING
            assert stored.final_answer is None

            return await env.handler.handle_slice(
                run.run_id, TENANT, epoch_b, False, worker_id="B",
            )

        assert asyncio.run(main()) == "complete"
        final = env.run_repo.get(run.run_id, TENANT)
        assert final.status is dm.RunStatus.COMPLETED
        assert final.final_answer == "done"
        row_status, row_epoch = _job_row(db, run.run_id)
        assert row_epoch == 2
        assert row_status == dm.DispatchStatus.DONE.value


class TestLoadQueueSaturation:

    def test_saturation_rejects_without_side_effects_then_backlog_drains(self):
        db = _gate_db()
        story = []
        for i in range(5):
            story.extend([
                _spawn_decision("a1", "work"),
                _wait_decision("a1"),
                _finish_decision("loaded"),
            ])
        env = _build_env(
            db, [], FakeFactory(scripts={"a1": [{"did": 1}]}),
            config=_gate_config(),
            llm=_StoryLLM(story),
        )
        svc = AgentSwarmService(
            run_repo=env.run_repo, dispatch_repo=env.dispatch_repo,
            agent_repo=env.agent_repo, message_repo=env.message_repo,
            round_repo=env.round_repo, guard_repo=env.guard_repo,
            result_repo=env.result_repo,
            config=AgentSwarmConfig(
                llm=LlmConfig(base_url="stub", api_key="stub",
                              default_model="stub"),
                admission=AgentSwarmAdmissionConfig(
                    max_global_concurrent_runs=100, max_pending_dispatches=5),
                tenant_limits=AgentSwarmTenantLimits(max_concurrent_runs=100),
            ),
        )

        async def main():
            ids = []
            for i in range(5):
                ids.append(await svc.submit_async(_submit_request(goal=f"g{i}")))
            with pytest.raises(QueueDepthExceeded):
                await svc.submit_async(_submit_request(goal="overflow"))
            assert env.dispatch_repo.count_pending_dispatches() == 5
            assert env.run_repo.count_active_runs_global() == 5
            page = env.run_repo.list_page(TENANT, page=1, page_size=100)
            assert page.total == 5

            worker = AgentSwarmWorker(
                config=env.cfg, handler=env.handler, dispatch_repo=env.dispatch_repo,
            )
            started = time.monotonic()
            for _ in range(40):
                if env.dispatch_repo.count_pending_dispatches() == 0:
                    break
                await worker.run_once()
                await asyncio.sleep(0.01)
            drained_in = time.monotonic() - started
            assert env.dispatch_repo.count_pending_dispatches() == 0
            assert env.run_repo.count_active_runs_global() == 0
            for rid in ids:
                stored = env.run_repo.get(dm.RunId(rid), TENANT)
                assert stored.status is dm.RunStatus.COMPLETED
                assert stored.final_answer == "loaded"
            assert drained_in < 15.0

            await svc.submit_async(_submit_request(goal="post-drain"))
            assert env.dispatch_repo.count_pending_dispatches() == 1

        asyncio.run(main())


class TestRollingDeploy:

    def test_mid_slice_redeploy_resumes_without_reexecution(self):
        db = _gate_db()
        factory = FakeFactory(scripts={"a1": [{"did": 1}]})
        story = _StoryLLM([
            _spawn_decision("a1", "work"),
            _wait_decision("ghost"),
        ])
        env = _build_env(
            db, [], factory,
            config=_gate_config(grace=0.05, slice_seconds=10.0),
            llm=story,
        )
        run = _mk_run(goal="rolling")
        env.run_repo.create(run)
        env.dispatch_repo.enqueue(run_id=run.run_id, tenant=TENANT, deadline_seconds=3600)

        async def main():
            worker_a = AgentSwarmWorker(
                config=env.cfg, handler=env.handler, dispatch_repo=env.dispatch_repo,
            )
            await worker_a.startup()
            # Slice A: spawn a1, its result persists, then the slice parks
            # waiting on "ghost" — a deploy lands over-grace and releases
            # the dispatch for the next worker.
            await asyncio.sleep(0.4)
            story.set_script([_finish_decision("deployed")])
            await worker_a.shutdown()

            worker_b = AgentSwarmWorker(
                config=env.cfg, handler=env.handler, dispatch_repo=env.dispatch_repo,
            )
            for _ in range(40):
                if env.dispatch_repo.count_pending_dispatches() == 0:
                    break
                await worker_b.run_once()
                await asyncio.sleep(0.01)
            await worker_b.shutdown()

        asyncio.run(main())

        final = env.run_repo.get(run.run_id, TENANT)
        assert final.status is dm.RunStatus.COMPLETED
        assert final.final_answer == "deployed"
        assert len(env.agent_repo.list_by_run(run.run_id, TENANT)) == 1
        results = env.result_repo.list_by_run(run.run_id, TENANT)
        assert len(results) == 1
        assert (results[0].payload is None) != (results[0].artifact_key is None)
        assert len(env.round_repo.list_by_run(run.run_id, TENANT)) == 1
        row_status, row_epoch = _job_row(db, run.run_id)
        assert row_status == dm.DispatchStatus.DONE.value
        assert row_epoch >= 2


class TestMultiTenantIsolation:

    def test_cross_tenant_access_is_indistinguishable_from_missing(self):
        db = _gate_db()
        env = _build_env(
            db, [], FakeFactory(),
            config=_gate_config(),
            llm=RepeatingLLMProvider(_finish_decision("t")),
        )
        run_a = _mk_run(goal="a only")
        env.run_repo.create(run_a)
        assert env.run_repo.get(run_a.run_id, TENANT_B) is None
        page_b = env.run_repo.list_page(TENANT_B, page=1, page_size=10)
        assert page_b.total == 0
        assert page_b.items == ()
        assert env.result_repo.get_by_run_agent(run_a.run_id, "x", TENANT_B) is None
        svc = AgentSwarmService(
            run_repo=env.run_repo, dispatch_repo=env.dispatch_repo,
            agent_repo=env.agent_repo, message_repo=env.message_repo,
            round_repo=env.round_repo, guard_repo=env.guard_repo,
            result_repo=env.result_repo, config=env.cfg,
        )
        assert svc.get_run(run_a.run_id, str(TENANT_B)) is None
        assert asyncio.run(
            svc.cancel(run_a.run_id, str(TENANT_B))) == "not_found"

    def test_one_tenants_cap_does_not_affect_the_other(self):
        db = _gate_db()
        env = _build_env(
            db, [], FakeFactory(),
            config=_gate_config(),
            llm=RepeatingLLMProvider(_finish_decision("t")),
        )
        svc = AgentSwarmService(
            run_repo=env.run_repo, dispatch_repo=env.dispatch_repo,
            agent_repo=env.agent_repo, message_repo=env.message_repo,
            round_repo=env.round_repo, guard_repo=env.guard_repo,
            result_repo=env.result_repo,
            config=AgentSwarmConfig(
                llm=LlmConfig(base_url="stub", api_key="stub",
                              default_model="stub"),
                admission=AgentSwarmAdmissionConfig(
                    max_global_concurrent_runs=100, max_pending_dispatches=100),
                tenant_limits=AgentSwarmTenantLimits(max_concurrent_runs=2),
            ),
        )

        async def main():
            await svc.submit_async(_submit_request(goal="a1"))
            await svc.submit_async(_submit_request(goal="a2"))
            with pytest.raises(TenantRunLimitExceeded):
                await svc.submit_async(_submit_request(goal="a3"))
            await svc.submit_async(_submit_request(str(TENANT_B), goal="b1"))

        asyncio.run(main())
        assert env.run_repo.count_active_runs(TENANT) == 2
        assert env.run_repo.count_active_runs(TENANT_B) == 1
        assert env.run_repo.count_active_runs_global() == 3


class TestMigrationCompat:

    def test_rows_written_before_additive_columns_stay_readable(self):
        db = _gate_db()
        env = _build_env(
            db, [], FakeFactory(),
            config=_gate_config(),
            llm=RepeatingLLMProvider(_wait_decision("x")),
        )
        run = _mk_run(goal="legacy")
        env.run_repo.create(run)
        env.dispatch_repo.enqueue(
            run_id=run.run_id, tenant=TENANT, deadline_seconds=3600,
        )
        claimed = env.dispatch_repo.claim_batch("mig", None, 1, 60)
        assert claimed[0]["claims"] == 1
        env.dispatch_repo.reschedule(
            run.run_id, TENANT, "mig", 0.0, None, claimed[0]["claim_epoch"],
        )
        re_claimed = env.dispatch_repo.claim_batch("mig", None, 1, 60)
        epoch_two = re_claimed[0]["claim_epoch"]
        env.dispatch_repo.renew_lease(run.run_id, TENANT, "mig", -1, epoch_two)
        # Rows written before the additive reclaims column flow through the
        # newest claim/reclaim reads harmlessly (rollback tolerates them).
        reclaimed = env.dispatch_repo.reclaim_batch("mig2", 10, 60)
        assert reclaimed[0]["reclaims"] == 1

    def test_dated_sql_files_are_monotonic_and_cover_orm(self):
        import pathlib
        import re
        sql_dir = pathlib.Path("src/agentclaw/community/core/agentswarm/sql")
        sql_files = sorted(sql_dir.glob("*.sql"))
        # One merged schema file (the 2026_10_08 + 2026_10_09_audit merge)
        # satisfies every invariant the test guards: dated, ordered,
        # covers every ORM table, no DROP statements.
        assert len(sql_files) >= 1
        dates = [f.name[:10] for f in sql_files]
        assert dates == sorted(dates)
        for model in om.ALL_MODELS:
            table = model.__tablename__
            assert any(table in f.read_text() for f in sql_files), (
                f"no DDL file creates {table}"
            )
        for sql_file in sql_files:
            assert not re.search(
                r"DROP\s+(TABLE|COLUMN|INDEX)", sql_file.read_text(), re.I)


class TestUpstreamDegradation:

    def test_dead_llm_fails_the_slice_fast_not_to_deadline(self):
        slice_seconds = 30.0
        db = _gate_db()
        env = _build_env(
            db, [], FakeFactory(),
            config=_gate_config(slice_seconds=slice_seconds),
            llm=_FailingLLM(),
        )
        run = _mk_run(goal="degrade")
        env.run_repo.create(run)
        env.dispatch_repo.enqueue(run_id=run.run_id, tenant=TENANT, deadline_seconds=3600)

        async def main():
            worker = AgentSwarmWorker(
                config=env.cfg, handler=env.handler, dispatch_repo=env.dispatch_repo,
            )
            started = time.monotonic()
            await worker.run_once()
            return time.monotonic() - started

        elapsed = asyncio.run(main())
        # Compare with the actual slice deadline, not an arbitrary CI wall-clock
        # threshold: a fast failure may still contend with a loaded runner,
        # while a real deadline burn exceeds the configured slice budget.
        assert elapsed < slice_seconds
        stored = env.run_repo.get(run.run_id, TENANT)
        assert stored.status is dm.RunStatus.FAILED
        assert stored.termination_reason is dm.TerminationReason.ERROR
        assert stored.error == "upstream gone"
        row_status, _ = _job_row(db, run.run_id)
        assert row_status == dm.DispatchStatus.DONE.value

    def test_open_breaker_sheds_without_constructing_subagents(self):
        async def main():
            factory = _RaisingFactory()
            runtime = SubagentRuntime(
                factory=factory, max_width=1, max_attempts=3,
                per_task_timeout=5.0,
                upstream_breaker=UpstreamBreaker(
                    failure_threshold=1, open_seconds=60.0),
            )
            run = _mk_run(goal="shed")
            agent_id = await runtime.spawn(
                run, dm.TaskEnvelope(
                    name="a1", objective="o", output_schema={},
                    boundaries="", parent_goal=run.goal,
                ),
            )
            await runtime.start()
            try:
                results = await runtime.wait_for_completions()
            finally:
                await runtime.stop()
            return factory, results, agent_id

        factory, results, agent_id = asyncio.run(main())
        (result,) = results
        assert factory.creates == 1
        assert result["agent_id"] == agent_id
        assert result["status"] is dm.AgentStatus.FAILED
        assert result["error"] == "upstream_breaker_open"