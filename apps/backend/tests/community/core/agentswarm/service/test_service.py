"""AgentSwarmService tests over real repositories (in-memory SQLite).

Covers idempotent submit join, tenant-scoped concurrency limits, real
list_runs pagination, missing-LLM fail-clear, and the cancel flow. Status
writes go through the fenced claim_epoch path (enqueue -> claim -> update).
"""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager


import pytest
from sqlalchemy import create_engine, select, func, update
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, AgentSwarmTenantLimits, LlmConfig,
)
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.repository.agent import AgentRepository
from agentclaw.community.core.agentswarm.repository.guard import GuardEventRepository
from agentclaw.community.core.agentswarm.repository.job import JobRepository
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.shard import ResultShardRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.core.agentswarm.service import AgentSwarmService
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentSwarmConfigError, AgentSwarmRunNotFoundError,
    AgentSwarmServiceProtocol, SubmitRunRequest, TenantRunLimitExceeded,
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
        om.AcAgentSwarmRunModel.__table__,
        om.AcAgentSwarmAgentModel.__table__,
        om.AcAgentSwarmMessageModel.__table__,
        om.AcAgentSwarmResultShardModel.__table__,
        om.AcAgentSwarmRoundModel.__table__,
        om.AcAgentSwarmGuardEventModel.__table__,
        om.AcAgentSwarmJobModel.__table__,
    ])
    yield _TestDB(engine)
    engine.dispose()


def _config(max_concurrent_runs: int = 8,
            llm: LlmConfig | None = None) -> AgentSwarmConfig:
    return AgentSwarmConfig(
        llm=llm if llm is not None else LlmConfig(base_url="http://llm-stub"),
        tenant_limits=AgentSwarmTenantLimits(
            max_concurrent_runs=max_concurrent_runs,
        ),
    )


@pytest.fixture()
def service(db) -> AgentSwarmService:
    return AgentSwarmService(
        run_repo=RunRepository(db),
        job_repo=JobRepository(db),
        agent_repo=AgentRepository(db),
        message_repo=MessageRepository(db),
        round_repo=RoundRepository(db),
        guard_repo=GuardEventRepository(db),
        shard_repo=ResultShardRepository(db),
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
        agents=(dm.AgentSpec(name="r1", role="research"),),
        idempotency_key=idempotency_key,
    )


def _db_count(db, model, tenant: str) -> int:
    with db.orm_session() as session:
        return session.execute(
            select(func.count()).select_from(model).where(model.tenant == tenant)
        ).scalar_one()


def _mk_run(status: dm.RunStatus = dm.RunStatus.RUNNING,
            ended_at: dm.EpochMillis | None = None) -> dm.Run:
    return dm.Run(
        run_id=dm.RunId(uuid.uuid4().hex),
        tenant=dm.TenantId("tenant-a"),
        goal="test goal",
        agent_catalog=(dm.AgentSpec(name="r1", role="research"),),
        provider=dm.ProviderKind("stub"),
        request_metadata=dm.RequestMetadata(),
        status=status,
        started_at=dm.EpochMillis(int(time.time() * 1000)),
        ended_at=ended_at,
    )


def _claim_one_job(job_repo: JobRepository, tenant: dm.TenantId, run_id: dm.RunId) -> int:
    claimed = job_repo.claim_batch("w1", [tenant], 10, 60)
    entry = next(c for c in claimed if c["run_id"] == run_id)
    return entry["claim_epoch"]


class TestProtocolConformance:

    def test_service_inherits_protocol(self, service):
        assert isinstance(service, AgentSwarmServiceProtocol)
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
        assert _db_count(job_repo._db, om.AcAgentSwarmRunModel, "tenant-a") == 1
        assert _db_count(job_repo._db, om.AcAgentSwarmJobModel, "tenant-a") == 1

    @pytest.mark.asyncio
    async def test_different_keys_create_new_runs(self, service, job_repo):
        await service.submit_async(_req("tenant-a", idempotency_key="k1"))
        await service.submit_async(_req("tenant-a", idempotency_key="k2"))
        assert _db_count(job_repo._db, om.AcAgentSwarmJobModel, "tenant-a") == 2


class TestTenantLimits:

    @pytest.mark.asyncio
    async def test_limit_refuses_second_concurrent_run(self, db):
        service = AgentSwarmService(
            run_repo=RunRepository(db), job_repo=JobRepository(db),
            agent_repo=AgentRepository(db), message_repo=MessageRepository(db),
            round_repo=RoundRepository(db), guard_repo=GuardEventRepository(db),
            shard_repo=ResultShardRepository(db),
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
        service = AgentSwarmService(
            run_repo=RunRepository(db), job_repo=JobRepository(db),
            agent_repo=AgentRepository(db), message_repo=MessageRepository(db),
            round_repo=RoundRepository(db), guard_repo=GuardEventRepository(db),
            shard_repo=ResultShardRepository(db),
            config=_config(max_concurrent_runs=1),
        )
        await service.submit_async(_req("tenant-a"))
        second = await service.submit_async(_req("tenant-b"))
        assert service.get_run(second, "tenant-b") is not None

    @pytest.mark.asyncio
    async def test_limit_recovers_after_terminal_status(self, db, run_repo, job_repo):
        service = AgentSwarmService(
            run_repo=RunRepository(db), job_repo=JobRepository(db),
            agent_repo=AgentRepository(db), message_repo=MessageRepository(db),
            round_repo=RoundRepository(db), guard_repo=GuardEventRepository(db),
            shard_repo=ResultShardRepository(db),
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
                agent_catalog=(dm.AgentSpec(name="r1", role="research"),),
                provider=dm.ProviderKind("stub"),
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
        service = AgentSwarmService(
            run_repo=RunRepository(db), job_repo=JobRepository(db),
            agent_repo=AgentRepository(db), message_repo=MessageRepository(db),
            round_repo=RoundRepository(db), guard_repo=GuardEventRepository(db),
            shard_repo=ResultShardRepository(db),
            config=AgentSwarmConfig(),
        )
        with pytest.raises(AgentSwarmConfigError, match="llm config missing"):
            await service.submit_async(_req("tenant-a"))
        assert _db_count(job_repo._db, om.AcAgentSwarmJobModel, "tenant-a") == 0

    @pytest.mark.asyncio
    async def test_empty_base_url_refuses_submit(self, db):
        service = AgentSwarmService(
            run_repo=RunRepository(db), job_repo=JobRepository(db),
            agent_repo=AgentRepository(db), message_repo=MessageRepository(db),
            round_repo=RoundRepository(db), guard_repo=GuardEventRepository(db),
            shard_repo=ResultShardRepository(db),
            config=_config(llm=LlmConfig(base_url="", api_key="k")),
        )
        with pytest.raises(AgentSwarmConfigError, match="base_url/api_key"):
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
        service = AgentSwarmService(
            run_repo=RunRepository(db), job_repo=JobRepository(db),
            agent_repo=AgentRepository(db), message_repo=MessageRepository(db),
            round_repo=RoundRepository(db), guard_repo=GuardEventRepository(db),
            shard_repo=ResultShardRepository(db),
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


class TestEnqueueFailureCompensation:
    """Orphan-run prevention: when enqueue raises AFTER this call created
    the run row, the RUNNING row would hold the tenant admission slot and
    the active idempotency key forever with no job for a worker to claim.
    The service must compensate (terminal FAILED frees both) and re-raise
    the original error — never swallow it."""

    @pytest.mark.asyncio
    async def test_enqueue_raise_marks_run_failed_frees_key_and_reraises(
            self, service, job_repo, monkeypatch):
        real_enqueue = job_repo.enqueue

        def enqueue_then_glitch(**kwargs):
            # DB glitch with an ambiguous commit: the job INSERT lands,
            # but the call surfaces an error to the submit plane anyway.
            real_enqueue(**kwargs)
            raise RuntimeError("db glitch during enqueue")

        monkeypatch.setattr(service._job_repo, "enqueue", enqueue_then_glitch)
        with pytest.raises(RuntimeError, match="db glitch"):
            await service.submit_async(
                _req("tenant-a", idempotency_key="idem-enq")
            )

        (run,) = service.list_runs("tenant-a").items
        assert run.status is dm.RunStatus.FAILED
        assert run.error is not None and "enqueue" in run.error
        assert run.ended_at is not None
        # Idempotency slot freed: the duplicate-submit join finds nothing.
        assert service._run_repo.get_by_idempotency(
            "idem-enq", dm.TenantId("tenant-a"),
        ) is None
        # Admission freed: the orphan no longer occupies a tenant slot.
        assert service._run_repo.count_active_runs(dm.TenantId("tenant-a")) == 0
        # Functional proof both: the same key opens a fresh run once the
        # job plane recovers.
        monkeypatch.setattr(service._job_repo, "enqueue", real_enqueue)
        retry = await service.submit_async(
            _req("tenant-a", idempotency_key="idem-enq")
        )
        assert retry != run.run_id
        assert service._run_repo.count_active_runs(dm.TenantId("tenant-a")) == 1

    @pytest.mark.asyncio
    async def test_enqueue_raise_without_job_row_fails_run_frees_slots_and_reraises(
            self, service, db, monkeypatch):
        # The common failure shape: enqueue raised BEFORE the job row
        # committed (DB down / disk full / deadlock → rollback), so NO
        # job row exists and the D28-fenced update_status(claim_epoch=0)
        # would match zero rows. The submit plane must still reclaim the
        # run it just created through the unfenced fail_unclaimed seam:
        # run FAILED, key freed, admission freed — and the original
        # error propagates.
        def enqueue_glitch(**kwargs):
            raise RuntimeError("db down before insert")

        monkeypatch.setattr(service._job_repo, "enqueue", enqueue_glitch)
        with pytest.raises(RuntimeError, match="db down"):
            await service.submit_async(
                _req("tenant-a", idempotency_key="idem-orphan")
            )
        assert _db_count(db, om.AcAgentSwarmJobModel, "tenant-a") == 0
        (run,) = service.list_runs("tenant-a").items
        assert run.status is dm.RunStatus.FAILED
        assert run.ended_at is not None
        assert run.error is not None and "enqueue" in run.error
        # Idempotency slot freed: the duplicate-submit join finds nothing.
        assert service._run_repo.get_by_idempotency(
            "idem-orphan", dm.TenantId("tenant-a"),
        ) is None
        # Admission freed: the orphan no longer occupies a tenant slot.
        assert service._run_repo.count_active_runs(
            dm.TenantId("tenant-a"),
        ) == 0

    @pytest.mark.asyncio
    async def test_failed_compensation_write_does_not_mask_original_error(
            self, service, job_repo, monkeypatch):
        # The compensation write can fail on the same broken DB; it must
        # not replace or hide the original enqueue exception.
        real_enqueue = job_repo.enqueue

        def enqueue_then_glitch(**kwargs):
            real_enqueue(**kwargs)
            raise RuntimeError("original enqueue error")

        def broken_fail_unclaimed(*args, **kwargs):
            raise ValueError("compensation path also broken")

        monkeypatch.setattr(service._job_repo, "enqueue", enqueue_then_glitch)
        monkeypatch.setattr(
            service._run_repo, "fail_unclaimed", broken_fail_unclaimed,
        )
        with pytest.raises(RuntimeError, match="original enqueue error"):
            await service.submit_async(_req("tenant-a"))


class TestFailUnclaimedSeam:
    """Repository-level contract of fail_unclaimed — the unfenced
    submit-plane seam the service's enqueue-failure compensation uses.
    Unlike the D28-fenced update_status (which requires a job row at the
    caller's claim_epoch), fail_unclaimed reclaims a RUNNING run with NO
    job row at all: the job INSERT may never have committed. The
    status==RUNNING predicate is the only clobber guard — an already
    terminal or deleted run refuses the write."""

    def test_fails_running_run_without_job_row_clearing_key_and_slots(
            self, run_repo):
        run = _mk_run()
        run.active_idempotency_key = dm.IdempotencyKey("key-seam")
        run_repo.create(run)
        assert run_repo.fail_unclaimed(
            run.run_id, dm.TenantId("tenant-a"), "boom",
        ) is True
        got = run_repo.get(run.run_id, dm.TenantId("tenant-a"))
        assert got.status is dm.RunStatus.FAILED
        assert got.error == "boom"
        assert got.ended_at is not None
        assert got.active_idempotency_key is None
        # The idempotency slot is freed for a fresh submit with that key.
        assert run_repo.get_by_idempotency(
            "key-seam", dm.TenantId("tenant-a"),
        ) is None
        assert run_repo.count_active_runs(dm.TenantId("tenant-a")) == 0

    def test_already_terminal_run_returns_false_and_preserves_status(
            self, run_repo):
        run = _mk_run(
            status=dm.RunStatus.COMPLETED, ended_at=dm.EpochMillis(123),
        )
        run_repo.create(run)
        assert run_repo.fail_unclaimed(
            run.run_id, dm.TenantId("tenant-a"), "late compensation",
        ) is False
        got = run_repo.get(run.run_id, dm.TenantId("tenant-a"))
        assert got.status is dm.RunStatus.COMPLETED
        assert got.error is None
        assert got.ended_at == dm.EpochMillis(123)

    def test_deleted_run_returns_false(self, run_repo, db):
        run = _mk_run()
        run_repo.create(run)
        with db.orm_session() as session:
            session.execute(
                update(om.AcAgentSwarmRunModel).where(
                    om.AcAgentSwarmRunModel.run_id == run.run_id,
                ).values(is_deleted=True)
            )
        assert run_repo.fail_unclaimed(
            run.run_id, dm.TenantId("tenant-a"), "gone",
        ) is False
        assert run_repo.get(run.run_id, dm.TenantId("tenant-a")) is None


class TestRunEvents:

    @pytest.mark.asyncio
    async def test_missing_run_raises_not_found(self, service):
        with pytest.raises(AgentSwarmRunNotFoundError):
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
