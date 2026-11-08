"""submit_sync deadline discipline and purge_run active-run guard.

Real repositories over in-memory SQLite (the module's house style):

- The submit_sync deadline is a float with a one-second floor: int()
  truncated a fractional ``slice_max_seconds`` (0.5 -> 0) so the wait
  returned instantly, and on expiry it fabricated a never-persisted
  ``Run(TIMED_OUT, started_at=0)``. The wait now honors fractional
  budgets and returns only real rows; a run that vanishes mid-wait
  surfaces as AgentSwarmSyncTimeoutError, never a synthetic Run.
- purge_run refuses a RUNNING run: the seven-table erase is
  non-transactional and would race an in-flight claimed slice's fenced
  writes. Terminal runs (and their jobs) still erase; a missing run
  stays None.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, AgentSwarmOrchestrationConfig, LlmConfig,
)
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.repository.agent import AgentRepository
from agentclaw.community.core.agentswarm.repository.guard import GuardEventRepository
from agentclaw.community.core.agentswarm.repository.run_dispatch import RunDispatchRepository
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.core.agentswarm.repository.agent_result import AgentResultRepository
from agentclaw.community.core.agentswarm.service import AgentSwarmService
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentSwarmRunNotPurgeableError, AgentSwarmSyncTimeoutError,
    SubmitRunRequest,
)

TENANT = "tenant-a"


class _TestDB:
    def __init__(self, engine):
        self._factory = sessionmaker(
            bind=engine, autocommit=False, autoflush=False,
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


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
    yield _TestDB(engine)
    engine.dispose()


def _service(db, *, slice_max_seconds: float = 300.0) -> AgentSwarmService:
    config = AgentSwarmConfig(
        llm=LlmConfig(base_url="stub"),
        orchestration=AgentSwarmOrchestrationConfig(
            slice_max_seconds=slice_max_seconds,
        ),
    )
    return AgentSwarmService(
        run_repo=RunRepository(db), dispatch_repo=RunDispatchRepository(db),
        agent_repo=AgentRepository(db), message_repo=MessageRepository(db),
        round_repo=RoundRepository(db), guard_repo=GuardEventRepository(db),
        result_repo=AgentResultRepository(db), config=config,
    )


def _req(idempotency_key: str | None = None) -> SubmitRunRequest:
    return SubmitRunRequest(
        tenant=TENANT,
        goal="sync-wait goal",
        agents=(dm.AgentSpec(name="r1", role="research"),),
        idempotency_key=idempotency_key,
    )


def _claim_one(dispatch_repo: RunDispatchRepository, run_id: str) -> int:
    claimed = dispatch_repo.claim_batch("w1", [dm.TenantId(TENANT)], 10, 60)
    entry = next(c for c in claimed if c["run_id"] == dm.RunId(run_id))
    return entry["claim_epoch"]


def _count(db, model, run_id: str) -> int:
    with db.orm_session() as session:
        return session.execute(
            select(func.count()).select_from(model).where(
                model.run_id == dm.RunId(run_id),
                model.tenant == dm.TenantId(TENANT),
            )
        ).scalar_one()


class TestDeadlineFloor:

    def test_fractional_budget_is_kept_not_truncated(self, db):
        assert _service(db, slice_max_seconds=2.5)._deadline == 2.5

    def test_sub_second_budget_is_floored_at_one_poll_cycle(self, db):
        assert _service(db, slice_max_seconds=0.5)._deadline == 1.0

    def test_zero_budget_takes_the_default_deadline(self, db):
        assert _service(db, slice_max_seconds=0)._deadline == 3600.0


class TestSyncWaitNeverFabricates:

    @pytest.mark.asyncio
    async def test_fractional_budget_does_not_return_instantly(self, db):
        # int(0.05) == 0 made the old poll loop unreachable: submit_sync
        # returned in microseconds. The floored deadline keeps at least
        # one full poll cycle alive.
        svc = _service(db, slice_max_seconds=0.05)
        req = _req(idempotency_key="frac-1")
        run_id = await svc.submit_async(req)

        started = time.monotonic()
        synced = await svc.submit_sync(req)
        elapsed = time.monotonic() - started

        assert elapsed >= 0.5
        assert synced.run_id == dm.RunId(run_id)
        assert synced.status is dm.RunStatus.RUNNING
        assert synced.started_at != dm.EpochMillis(0)
        stored = svc.get_run(run_id, TENANT)
        assert stored is not None
        assert stored.status is dm.RunStatus.RUNNING

    @pytest.mark.asyncio
    async def test_gone_run_at_deadline_raises_typed_error(self, db):
        from sqlalchemy import update

        svc = _service(db, slice_max_seconds=1.0)
        req = _req(idempotency_key="frac-vanish")
        run_id = await svc.submit_async(req)

        async def delete_mid_poll():
            await asyncio.sleep(0.05)
            with db.orm_session() as session:
                session.execute(
                    update(om.AcAgentSwarmRunModel).where(
                        om.AcAgentSwarmRunModel.run_id == dm.RunId(run_id),
                    ).values(is_deleted=True)
                )

        deleter = asyncio.create_task(delete_mid_poll())
        with pytest.raises(AgentSwarmSyncTimeoutError) as caught:
            await svc.submit_sync(req)
        await deleter
        assert caught.value.run_id == run_id
        assert caught.value.deadline == 1.0


class TestPurgeRunGuard:

    @pytest.mark.asyncio
    async def test_running_run_with_live_lease_is_refused(self, db):
        svc = _service(db)
        run_id = await svc.submit_async(_req(idempotency_key="purge-live"))
        _claim_one(svc._dispatch_repo, run_id)

        with pytest.raises(AgentSwarmRunNotPurgeableError) as caught:
            svc.purge_run(run_id, TENANT)
        assert caught.value.run_id == run_id
        assert caught.value.status is dm.RunStatus.RUNNING

        assert svc.get_run(run_id, TENANT) is not None
        assert _count(db, om.AcAgentSwarmRunModel, run_id) == 1
        assert _count(db, om.AcAgentSwarmRunDispatchModel, run_id) == 1

    @pytest.mark.asyncio
    async def test_terminal_run_still_erases_rows(self, db):
        svc = _service(db)
        run_id = await svc.submit_async(_req(idempotency_key="purge-dead"))
        epoch = _claim_one(svc._dispatch_repo, run_id)
        assert svc._run_repo.update_status(
            dm.RunId(run_id), dm.TenantId(TENANT), dm.RunStatus.COMPLETED,
            termination_reason=dm.TerminationReason.FINISH,
            final_answer="done", claim_epoch=epoch,
        )

        counts = svc.purge_run(run_id, TENANT)
        assert counts == {
            "agents": 0, "messages": 0, "results": 0, "rounds": 0,
            "guards": 0, "dispatches": 1, "run": 1,
        }
        assert svc.get_run(run_id, TENANT) is None
        assert svc.purge_run(run_id, TENANT) is None

    def test_missing_run_purge_stays_none(self, db):
        assert _service(db).purge_run("nope", TENANT) is None