"""AgentComputeService tests over real repositories (in-memory SQLite).

Covers idempotent submit join, tenant-scoped concurrency limits, real
list_runs pagination, missing-LLM fail-clear, and the cancel flow. Status
writes go through the fenced claim_epoch path (enqueue -> claim -> update).
"""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentcompute.config.settings import (
    AgentComputeConfig, AgentComputeTenantLimits, LlmConfig,
)
from agentclaw.community.core.agentcompute.domain import models as dm
from agentclaw.community.core.agentcompute.repository import models as om
from agentclaw.community.core.agentcompute.repository.impl import (
    AgentRepository, GuardEventRepository, JobRepository, RoundRepository,
    RunRepository,
)
from agentclaw.community.core.agentcompute.service import AgentComputeService
from agentclaw.community.core.agentcompute.service_protocol import (
    AgentComputeConfigError, AgentComputeRunNotFoundError,
    AgentComputeServiceProtocol, SubmitRunRequest, TenantRunLimitExceeded,
)


class _TestDB:
    def __init__(self, engine):
        self._factory = sessionmaker(
            bind=engine, autocommit=False, autoflush=False
        )

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

    session = orm_session


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        om.AcAgentComputeRunModel.__table__,
        om.AcAgentComputeAgentModel.__table__,
        om.AcAgentComputeMessageModel.__table__,
        om.AcAgentComputeResultShardModel.__table__,
        om.AcAgentComputeRoundModel.__table__,
        om.AcAgentComputeGuardEventModel.__table__,
        om.AcAgentComputeJobModel.__table__,
    ])
    yield _TestDB(engine)
    engine.dispose()


def _config(max_concurrent_runs: int = 8,
            llm: LlmConfig | None = None) -> AgentComputeConfig:
    return AgentComputeConfig(
        llm=llm if llm is not None else LlmConfig(base_url="http://llm-stub"),
        tenant_limits=AgentComputeTenantLimits(
            max_concurrent_runs=max_concurrent_runs,
        ),
    )


@pytest.fixture()
def service(db) -> AgentComputeService:
    return AgentComputeService(
        run_repo=RunRepository(db),
        job_repo=JobRepository(db),
        agent_repo=AgentRepository(db),
        round_repo=RoundRepository(db),
        guard_repo=GuardEventRepository(db),
        config=_config(),
    )


@pytest.fixture()
def job_repo(db) -> JobRepository:
    return JobRepository(db)


@pytest.fixture()
def run_repo(db) -> RunRepository:
    return RunRepository(db)


def _req(tenant: str, idempotency_key: str | None = None) -> SubmitRunRequest:
    return SubmitRunRequest(
        tenant=tenant,
        goal="test goal",
        agents=(dm.AgentSpecRaw(name="r1", role="research"),),
        idempotency_key=idempotency_key,
    )


def _db_count(db, model, tenant: str) -> int:
    with db.orm_session() as session:
        return session.execute(
            select(func.count()).select_from(model).where(model.tenant == tenant)
        ).scalar_one()


def _claim_one_job(job_repo: JobRepository, tenant: dm.TenantId, run_id: dm.RunId) -> int:
    claimed = job_repo.claim_batch("w1", [tenant], 10, 60)
    entry = next(c for c in claimed if c["run_id"] == run_id)
    return entry["claim_epoch"]


class TestProtocolConformance:

    def test_service_inherits_protocol(self, service):
        assert isinstance(service, AgentComputeServiceProtocol)
        assert not getattr(service, "__abstractmethods__", set())


class TestIdempotentSubmit:

    @pytest.mark.asyncio
    async def test_duplicate_submit_joins_same_run(self, service, job_repo):
        req = _req("tenant-a", idempotency_key="idem-1")
        first = await service.submit_async(req)
        second = await service.submit_async(
            _req("tenant-a", idempotency_key="idem-1")
        )
        assert first == second
        assert _db_count(job_repo._db, om.AcAgentComputeRunModel, "tenant-a") == 1
        assert _db_count(job_repo._db, om.AcAgentComputeJobModel, "tenant-a") == 1

    @pytest.mark.asyncio
    async def test_different_keys_create_new_runs(self, service, job_repo):
        await service.submit_async(_req("tenant-a", idempotency_key="k1"))
        await service.submit_async(_req("tenant-a", idempotency_key="k2"))
        assert _db_count(job_repo._db, om.AcAgentComputeJobModel, "tenant-a") == 2


class TestTenantLimits:

    @pytest.mark.asyncio
    async def test_limit_refuses_second_concurrent_run(self, db):
        service = AgentComputeService(
            run_repo=RunRepository(db), job_repo=JobRepository(db),
            agent_repo=AgentRepository(db), round_repo=RoundRepository(db),
            guard_repo=GuardEventRepository(db),
            config=_config(max_concurrent_runs=1),
        )
        first = await service.submit_async(_req("tenant-a"))
        assert first
        with pytest.raises(TenantRunLimitExceeded) as caught:
            await service.submit_async(_req("tenant-a"))
        assert caught.value.tenant == "tenant-a"
        assert caught.value.limit == 1

    @pytest.mark.asyncio
    async def test_other_tenant_unaffected_by_tenant_a_limit(self, db):
        service = AgentComputeService(
            run_repo=RunRepository(db), job_repo=JobRepository(db),
            agent_repo=AgentRepository(db), round_repo=RoundRepository(db),
            guard_repo=GuardEventRepository(db),
            config=_config(max_concurrent_runs=1),
        )
        await service.submit_async(_req("tenant-a"))
        second = await service.submit_async(_req("tenant-b"))
        assert service.get_run(second, "tenant-b") is not None

    @pytest.mark.asyncio
    async def test_limit_recovers_after_terminal_status(self, db, run_repo, job_repo):
        service = AgentComputeService(
            run_repo=RunRepository(db), job_repo=JobRepository(db),
            agent_repo=AgentRepository(db), round_repo=RoundRepository(db),
            guard_repo=GuardEventRepository(db),
            config=_config(max_concurrent_runs=1),
        )
        run_id = await service.submit_async(_req("tenant-a"))
        epoch = _claim_one_job(job_repo, dm.TenantId("tenant-a"), dm.RunId(run_id))
        assert run_repo.update_status(
            dm.RunId(run_id), dm.TenantId("tenant-a"), dm.RunStatus.COMPLETED,
            termination_reason=dm.TerminationReason.FINISH, final_answer="done",
            claim_epoch=epoch,
        )
        replacement = await service.submit_async(_req("tenant-a"))
        assert replacement != run_id


class TestListRuns:

    def _seed_runs(self, run_repo: RunRepository, job_repo: JobRepository,
                   count: int, status: dm.RunStatus) -> list[dm.RunId]:
        ids = []
        for i in range(count):
            run_id = dm.RunId(uuid.uuid4().hex)
            run = dm.Run(
                run_id=run_id,
                tenant=dm.TenantId("tenant-a"),
                goal=f"goal-{i}",
                agent_catalog=(dm.AgentSpecRaw(name="r1", role="research"),),
                provider=dm.ProviderId("stub"),
                request_metadata=dm.RequestMetadata(),
                status=status,
                started_at=dm.EpochMillis(int(time.time() * 1000)),
            )
            run_repo.create(run)
            job_repo.enqueue(
                run_id=run_id, tenant=dm.TenantId("tenant-a"), deadline_seconds=60,
            )
            ids.append(run_id)
        return ids

    def test_pagination_over_real_repository(self, service, run_repo, job_repo):
        self._seed_runs(run_repo, job_repo, 3, dm.RunStatus.COMPLETED)
        self._seed_runs(run_repo, job_repo, 2, dm.RunStatus.FAILED)
        page1 = service.list_runs("tenant-a", page=1, page_size=4)
        assert page1.total == 5
        assert len(page1.items) == 4
        page2 = service.list_runs("tenant-a", page=2, page_size=4)
        assert len(page2.items) == 1
        assert {r.run_id for r in page1.items}.isdisjoint(
            {r.run_id for r in page2.items}
        )

    def test_status_filter_is_typed(self, service, run_repo, job_repo):
        self._seed_runs(run_repo, job_repo, 2, dm.RunStatus.COMPLETED)
        self._seed_runs(run_repo, job_repo, 1, dm.RunStatus.FAILED)
        failed = service.list_runs("tenant-a", dm.RunStatus.FAILED)
        assert failed.total == 1
        assert all(r.status is dm.RunStatus.FAILED for r in failed.items)

    def test_list_is_tenant_scoped(self, service, run_repo, job_repo):
        self._seed_runs(run_repo, job_repo, 2, dm.RunStatus.COMPLETED)
        assert service.list_runs("tenant-b").total == 0


class TestConfigFailClear:

    @pytest.mark.asyncio
    async def test_missing_llm_config_refuses_submit(self, db, job_repo):
        service = AgentComputeService(
            run_repo=RunRepository(db), job_repo=JobRepository(db),
            agent_repo=AgentRepository(db), round_repo=RoundRepository(db),
            guard_repo=GuardEventRepository(db),
            config=AgentComputeConfig(),
        )
        with pytest.raises(AgentComputeConfigError, match="llm config missing"):
            await service.submit_async(_req("tenant-a"))
        assert _db_count(job_repo._db, om.AcAgentComputeJobModel, "tenant-a") == 0

    @pytest.mark.asyncio
    async def test_empty_base_url_refuses_submit(self, db):
        service = AgentComputeService(
            run_repo=RunRepository(db), job_repo=JobRepository(db),
            agent_repo=AgentRepository(db), round_repo=RoundRepository(db),
            guard_repo=GuardEventRepository(db),
            config=_config(llm=LlmConfig(base_url="", api_key="k")),
        )
        with pytest.raises(AgentComputeConfigError, match="base_url/api_key"):
            await service.submit_async(_req("tenant-a"))


class TestCancelFlow:

    @pytest.mark.asyncio
    async def test_cancel_unknown_run_is_not_found(self, service):
        assert await service.cancel("nope", "tenant-a") == "not_found"

    @pytest.mark.asyncio
    async def test_cancel_running_run_reports_running(self, service):
        run_id = await service.submit_async(_req("tenant-a"))
        assert await service.cancel(run_id, "tenant-a") == "RUNNING"

    @pytest.mark.asyncio
    async def test_cancel_after_terminal_reports_terminal_status(
        self, db, run_repo, job_repo,
    ):
        service = AgentComputeService(
            run_repo=RunRepository(db), job_repo=JobRepository(db),
            agent_repo=AgentRepository(db), round_repo=RoundRepository(db),
            guard_repo=GuardEventRepository(db),
            config=_config(),
        )
        run_id = await service.submit_async(_req("tenant-a"))
        epoch = _claim_one_job(job_repo, dm.TenantId("tenant-a"), dm.RunId(run_id))
        run_repo.update_status(
            dm.RunId(run_id), dm.TenantId("tenant-a"), dm.RunStatus.COMPLETED,
            termination_reason=dm.TerminationReason.FINISH,
            claim_epoch=epoch,
        )
        assert await service.cancel(run_id, "tenant-a") == "COMPLETED"


def _mk_agent(run_id: dm.RunId, name: str, status: dm.AgentStatus = dm.AgentStatus.QUEUED) -> dm.Agent:
    return dm.Agent(
        agent_id=dm.AgentId(uuid.uuid4().hex),
        run_id=run_id,
        tenant="tenant-a",
        parent_agent_id=None,
        depth=0,
        name=name,
        role=f"objective-{name}",
        kind=dm.AgentKind.LLM,
        task_spec=dm.TaskEnvelope(
            name=name, objective=f"objective-{name}",
            output_schema={}, boundaries="", parent_goal="test goal",
        ),
        spawn_round=0,
        status=status,
        attempts=0,
        idempotency_key=dm.IdempotencyKey(uuid.uuid4().hex),
        output={},
    )


def _mk_round(run_id: dm.RunId, cohort: tuple[dm.AgentId, ...]) -> dm.Round:
    return dm.Round(
        id=dm.RoundId(uuid.uuid4().hex),
        run_id=run_id,
        tenant="tenant-a",
        stage_index=0,
        orchestrator_steps=1,
        max_subagent_steps=0,
        cohort_agent_ids=cohort,
        started_at=dm.EpochMillis(0),
    )


def _mk_guard_event(run_id: dm.RunId, kind: dm.GuardKind) -> dm.GuardEvent:
    return dm.GuardEvent(
        id=dm.EventId(uuid.uuid4().hex),
        run_id=run_id,
        tenant="tenant-a",
        agent_id=None,
        kind=kind,
        details={},
        created_at=dm.EpochMillis(0),
    )


class TestRunEvents:

    @pytest.mark.asyncio
    async def test_missing_run_raises_not_found(self, service):
        with pytest.raises(AgentComputeRunNotFoundError):
            service.run_events("nope", "tenant-a")

    @pytest.mark.asyncio
    async def test_fresh_run_has_no_deltas_and_no_terminal(self, service, db):
        run_id = await service.submit_async(_req("tenant-a"))
        events = service.run_events(run_id, "tenant-a")
        assert events.agents == ()
        assert events.rounds == ()
        assert events.guards == ()
        assert events.run_status is dm.RunStatus.RUNNING
        assert events.terminal is None

    @pytest.mark.asyncio
    async def test_rows_are_returned_once_then_filtered_by_cursor(self, service, db):
        run_id = await service.submit_async(_req("tenant-a"))
        rid = dm.RunId(run_id)
        agent_repo = AgentRepository(db)
        round_repo = RoundRepository(db)
        guard_repo = GuardEventRepository(db)
        agent_repo.create(_mk_agent(rid, "a1"))
        agent_repo.create(_mk_agent(rid, "a2"))
        round_repo.create(_mk_round(rid, (dm.AgentId("x"), dm.AgentId("y"))))
        guard_repo.create(_mk_guard_event(rid, dm.GuardKind.DEDUPE_REJECT))

        events = service.run_events(run_id, "tenant-a")
        assert [a.name for a in events.agents] == ["a1", "a2"]
        cursors = [(a.row_cursor for a in events.agents)]
        assert list(cursors[0]) == sorted(a.row_cursor for a in events.agents)
        assert all(a.kind is dm.AgentKind.LLM for a in events.agents)
        assert all(a.status is dm.AgentStatus.QUEUED for a in events.agents)
        (round_event,) = events.rounds
        assert round_event.cohort_size == 2
        (guard_event,) = events.guards
        assert guard_event.kind is dm.GuardKind.DEDUPE_REJECT
        agent_cursor = events.agents[-1].row_cursor
        round_cursor = events.rounds[-1].row_cursor
        guard_cursor = events.guards[-1].row_cursor

        second = service.run_events(
            run_id, "tenant-a", after_agent_id=agent_cursor,
            after_round_id=round_cursor, after_event=guard_cursor,
        )
        assert second.agents == ()
        assert second.rounds == ()
        assert second.guards == ()
        assert second.run_status is dm.RunStatus.RUNNING
        assert second.terminal is None

    @pytest.mark.asyncio
    async def test_terminal_run_reports_run_status_and_terminal(self, service, db,
                                                                run_repo, job_repo):
        run_id = await service.submit_async(_req("tenant-a"))
        epoch = _claim_one_job(job_repo, dm.TenantId("tenant-a"), dm.RunId(run_id))
        run_repo.update_status(
            dm.RunId(run_id), dm.TenantId("tenant-a"), dm.RunStatus.COMPLETED,
            termination_reason=dm.TerminationReason.FINISH,
            claim_epoch=epoch,
        )
        events = service.run_events(run_id, "tenant-a")
        assert events.run_status is dm.RunStatus.COMPLETED
        assert events.terminal is dm.RunStatus.COMPLETED