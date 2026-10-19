"""Admission control tests (task 12.1): accept-or-reject submit semantics.

Typed 429-class errors (TenantRunLimitExceeded preserved; GlobalRunLimitExceeded
and QueueDepthExceeded added) raised BEFORE any row is created; idempotent
duplicate submits join existing runs even under full saturation; reject
metrics surface per reason. Real _TestDB, hand-fakes only.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.repository.agent import AgentRepository
from agentclaw.community.core.agentswarm.repository.guard import GuardEventRepository
from agentclaw.community.core.agentswarm.repository.run_dispatch import RunDispatchRepository
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.agent_result import AgentResultRepository
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmAdmissionConfig, AgentSwarmConfig,
    AgentSwarmTenantLimits, LlmConfig,
)
from agentclaw.community.core.agentswarm.service import AgentSwarmService
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentSwarmConfigError, GlobalRunLimitExceeded, QueueDepthExceeded,
    SubmitRunRequest, TenantRunLimitExceeded,
)


class _TestDB:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

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
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.__table__ for m in om.ALL_MODELS])
    yield _TestDB(engine)
    engine.dispose()




def _mk_req(tenant="t1", *, idem=None, goal="admission goal") -> SubmitRunRequest:
    return SubmitRunRequest(
        tenant=tenant,
        goal=goal,
        agents=(dm.AgentSpec(name="a1", role="research"),),
        idempotency_key=idem,
    )


def _mk_running_run(tenant: str) -> dm.Run:
    return dm.Run(
        run_id=dm.RunId(uuid.uuid4().hex),
        tenant=dm.TenantId(tenant),
        goal=uuid.uuid4().hex,
        agent_catalog=(dm.AgentSpec(name="a1", role="research"),),
        provider=dm.ProviderKind("stub"),
        request_metadata=dm.RequestMetadata(),
        status=dm.RunStatus.RUNNING,
        started_at=dm.EpochMillis(int(time.time() * 1000)),
    )


def _service(db, *, tenant_limit: int = 8, global_limit: int = 64,
             pending_limit: int = 500) -> AgentSwarmService:
    cfg = AgentSwarmConfig(
        llm=LlmConfig(base_url="stub"),
        tenant_limits=AgentSwarmTenantLimits(max_concurrent_runs=tenant_limit),
        admission=AgentSwarmAdmissionConfig(
            max_global_concurrent_runs=global_limit,
            max_pending_dispatches=pending_limit,
        ),
    )
    return AgentSwarmService(
        run_repo=RunRepository(db),
        dispatch_repo=RunDispatchRepository(db),
        agent_repo=AgentRepository(db),
        message_repo=MessageRepository(db),
        round_repo=RoundRepository(db),
        guard_repo=GuardEventRepository(db),
        result_repo=AgentResultRepository(db),
        config=cfg,
    )


class TestSubmitIdempotencyRace:

    def test_racing_duplicate_submits_join_without_second_job_row(
            self, db, monkeypatch):
        # The TOCTOU window itself: both submits miss get_by_idempotency
        # (delete the early join), both reach run create(); uk_run_idem
        # makes the loser converge onto the winner's run, and the loser
        # must NOT enqueue a second dispatch row for the same run.
        svc = _service(db)
        real_lookup = svc._run_repo.get_by_idempotency
        checks = {"misses": 0}

        def racey_lookup(key, tenant):
            # Calls 1-2 are the two early-join checks inside
            # submit_async — they miss, forcing both creates. Call 3
            # is create()'s convergence lookup after the failed INSERT:
            # by then the winner's row is committed and must be visible,
            # exactly as in the real cross-session race.
            checks["misses"] += 1
            if checks["misses"] <= 2:
                return None
            return real_lookup(key, tenant)

        monkeypatch.setattr(svc._run_repo, "get_by_idempotency", racey_lookup)
        first = asyncio.run(svc.submit_async(_mk_req("t1", idem="race-42")))
        second = asyncio.run(svc.submit_async(_mk_req("t1", idem="race-42")))
        assert second == first
        assert svc._run_repo.count_active_runs(dm.TenantId("t1")) == 1
        assert svc._dispatch_repo.count_pending_dispatches() == 1

    def test_early_join_still_skips_enqueue(self, db):
        # Un-patched join path: a duplicate submit found by
        # get_by_idempotency returns the same run_id and adds no dispatch
        # row (no IntegrityError window is even reached).
        svc = _service(db)
        first = asyncio.run(svc.submit_async(_mk_req("t1", idem="join-42")))
        second = asyncio.run(svc.submit_async(_mk_req("t1", idem="join-42")))
        assert second == first
        assert svc._run_repo.count_active_runs(dm.TenantId("t1")) == 1
        assert svc._dispatch_repo.count_pending_dispatches() == 1


class TestAdmissionControl:

    def test_tenant_reject_preserved_and_creates_no_rows(self, db):
        svc = _service(db, tenant_limit=2)
        assert asyncio.run(svc.submit_async(_mk_req("t1")))
        assert asyncio.run(svc.submit_async(_mk_req(
            "t1", goal="second goal")))

        with pytest.raises(TenantRunLimitExceeded) as exc_info:
            asyncio.run(svc.submit_async(_mk_req(
                "t1", goal="third goal")))

        assert exc_info.value.tenant == "t1"
        assert exc_info.value.limit == 2
        assert svc._run_repo.count_active_runs(dm.TenantId("t1")) == 2
        assert svc._run_repo.count_active_runs_global() == 2
        assert svc._dispatch_repo.count_pending_dispatches() == 2

    def test_global_reject_typed_and_creates_no_rows(self, db):
        # Three tenants each below their own cap saturate the global cap:
        # per-tenant fairness must not bypass fleet backpressure.
        svc = _service(db, tenant_limit=10, global_limit=3)
        for tenant in ("t1", "t2", "t3"):
            assert asyncio.run(svc.submit_async(_mk_req(tenant)))

        with pytest.raises(GlobalRunLimitExceeded) as exc_info:
            asyncio.run(svc.submit_async(_mk_req("t4")))

        assert exc_info.value.limit == 3
        assert svc._run_repo.count_active_runs_global() == 3
        assert svc._dispatch_repo.count_pending_dispatches() == 3

    def test_queue_depth_reject_typed_and_creates_no_rows(self, db):
        svc = _service(db, pending_limit=2)
        assert asyncio.run(svc.submit_async(_mk_req("t1")))
        assert asyncio.run(svc.submit_async(_mk_req("t2")))

        with pytest.raises(QueueDepthExceeded) as exc_info:
            asyncio.run(svc.submit_async(_mk_req("t3")))

        assert exc_info.value.limit == 2
        assert svc._dispatch_repo.count_pending_dispatches() == 2
        assert svc._run_repo.count_active_runs_global() == 2

    def test_idempotency_join_survives_all_saturation(self, db):
        svc = _service(db, tenant_limit=2, global_limit=2, pending_limit=2)
        first = asyncio.run(svc.submit_async(_mk_req("t1", idem="inc-42")))
        second = asyncio.run(svc.submit_async(_mk_req(
            "t2", idem="inc-43", goal="other goal")))
        # Fully saturated: global == 2, queue == 2, tenant t1 == 1/2.

        join_global = asyncio.run(svc.submit_async(_mk_req("t1", idem="inc-42")))
        join_queue = asyncio.run(svc.submit_async(
            _mk_req("t2", idem="inc-43", goal="other goal")))

        # A duplicate joins the existing run and consumes no new capacity.
        assert join_global == first
        assert join_queue == second
        assert svc._run_repo.count_active_runs_global() == 2
        assert svc._dispatch_repo.count_pending_dispatches() == 2
        joining = svc._run_repo.get_by_idempotency(
            "inc-43", dm.TenantId("t2"),
        )
        assert joining is not None and joining.run_id == second

    def test_new_key_under_saturation_is_not_rescued_by_dedup(self, db):
        svc = _service(db, pending_limit=1)
        asyncio.run(svc.submit_async(_mk_req("t1", idem="inc-42")))

        with pytest.raises(QueueDepthExceeded):
            asyncio.run(svc.submit_async(_mk_req(
                "t2", idem="inc-NEW", goal="distinct goal")))

    def test_accept_under_caps(self, db):
        svc = _service(db)
        run_id = asyncio.run(svc.submit_async(_mk_req("t1")))
        assert svc._run_repo.get(dm.RunId(run_id), dm.TenantId("t1")) is not None

    def test_llm_config_error_precedes_admission(self, db):
        run_repo = RunRepository(db)
        run_repo.create(_mk_running_run("t1"))
        cfg = AgentSwarmConfig(
            llm=None,
            tenant_limits=AgentSwarmTenantLimits(max_concurrent_runs=1),
            admission=AgentSwarmAdmissionConfig(
                max_global_concurrent_runs=1, max_pending_dispatches=1,
            ),
        )
        svc = AgentSwarmService(
            run_repo=run_repo, dispatch_repo=RunDispatchRepository(db),
            agent_repo=AgentRepository(db),
            message_repo=MessageRepository(db),
            round_repo=RoundRepository(db),
            guard_repo=GuardEventRepository(db),
            result_repo=AgentResultRepository(db), config=cfg,
        )

        # Fail-clear config rejection keeps precedence over 429-class caps.
        with pytest.raises(AgentSwarmConfigError):
            asyncio.run(svc.submit_async(_mk_req("t1")))