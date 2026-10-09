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
from agentclaw.community.core.agentcompute.domain import models as dm
from agentclaw.community.core.agentcompute.repository import models as om
from agentclaw.community.core.agentcompute.repository.impl import (
    AgentRepository, GuardEventRepository, JobRepository, MessageRepository,
    ResultShardRepository, RoundRepository, RunRepository,
)
from agentclaw.community.core.agentcompute.config.settings import (
    AgentComputeAdmissionConfig, AgentComputeConfig,
    AgentComputeTenantLimits, LlmConfig,
)
from agentclaw.community.core.agentcompute.service import AgentComputeService
from agentclaw.community.core.agentcompute.service_protocol import (
    AgentComputeConfigError, GlobalRunLimitExceeded, QueueDepthExceeded,
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


class RecordingMetrics:
    """Hand-fake MetricsPlugin: records every call, never raises."""

    def __init__(self) -> None:
        self.counters: list[tuple[str, dict | None]] = []
        self.observes: list[tuple[str, float, dict | None]] = []
        self.gauges: list[tuple[str, float, dict | None]] = []
        self.blocks: list[tuple[str, dict | None]] = []

    def increment(self, name, labels=None) -> None:
        self.counters.append((name, dict(labels) if labels else None))

    def observe(self, name, value, labels=None) -> None:
        self.observes.append((name, float(value), dict(labels) if labels else None))

    def set_gauge(self, name, value, labels=None) -> None:
        self.gauges.append((name, float(value), dict(labels) if labels else None))

    @contextmanager
    def time_block(self, name, labels=None):
        self.blocks.append((name, dict(labels) if labels else None))
        yield

    def admission_rejects(self) -> list[str]:
        return [labels["reason"] for name, labels in self.counters
                if name == "agentcompute_admission_reject"]


def _mk_req(tenant="t1", *, idem=None, goal="admission goal") -> SubmitRunRequest:
    return SubmitRunRequest(
        tenant=tenant,
        goal=goal,
        agents=(dm.AgentSpecRaw(name="a1", role="research"),),
        idempotency_key=idem,
    )


def _mk_running_run(tenant: str) -> dm.Run:
    return dm.Run(
        run_id=dm.RunId(uuid.uuid4().hex),
        tenant=dm.TenantId(tenant),
        goal=uuid.uuid4().hex,
        agent_catalog=(dm.AgentSpecRaw(name="a1", role="research"),),
        provider=dm.ProviderId("stub"),
        request_metadata=dm.RequestMetadata(),
        status=dm.RunStatus.RUNNING,
        started_at=dm.EpochMillis(int(time.time() * 1000)),
    )


def _service(db, *, tenant_limit: int = 8, global_limit: int = 64,
             pending_limit: int = 500, metrics=None) -> AgentComputeService:
    cfg = AgentComputeConfig(
        llm=LlmConfig(base_url="stub"),
        tenant_limits=AgentComputeTenantLimits(max_concurrent_runs=tenant_limit),
        admission=AgentComputeAdmissionConfig(
            max_global_concurrent_runs=global_limit,
            max_pending_jobs=pending_limit,
        ),
    )
    return AgentComputeService(
        run_repo=RunRepository(db),
        job_repo=JobRepository(db),
        agent_repo=AgentRepository(db),
        message_repo=MessageRepository(db),
        round_repo=RoundRepository(db),
        guard_repo=GuardEventRepository(db),
        shard_repo=ResultShardRepository(db),
        config=cfg,
        metrics=metrics,
    )


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
        assert svc._job_repo.count_pending_jobs() == 2

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
        assert svc._job_repo.count_pending_jobs() == 3

    def test_queue_depth_reject_typed_and_creates_no_rows(self, db):
        svc = _service(db, pending_limit=2)
        assert asyncio.run(svc.submit_async(_mk_req("t1")))
        assert asyncio.run(svc.submit_async(_mk_req("t2")))

        with pytest.raises(QueueDepthExceeded) as exc_info:
            asyncio.run(svc.submit_async(_mk_req("t3")))

        assert exc_info.value.limit == 2
        assert svc._job_repo.count_pending_jobs() == 2
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
        assert svc._job_repo.count_pending_jobs() == 2
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

    def test_reject_reasons_surface_through_metrics(self, db):
        metrics = RecordingMetrics()
        svc = _service(
            db, tenant_limit=1, global_limit=5, pending_limit=1, metrics=metrics,
        )
        assert asyncio.run(svc.submit_async(_mk_req("t1")))

        with pytest.raises(TenantRunLimitExceeded):
            asyncio.run(svc.submit_async(_mk_req(
                "t1", goal="tenant at cap")))

        with pytest.raises(QueueDepthExceeded):
            asyncio.run(svc.submit_async(_mk_req("t2")))

        for tenant in ("f1", "f2", "f3", "f4"):
            svc._run_repo.create(_mk_running_run(tenant))

        with pytest.raises(GlobalRunLimitExceeded):
            asyncio.run(svc.submit_async(_mk_req("t3")))

        assert metrics.admission_rejects() == ["tenant", "queue", "global"]

    def test_accept_under_caps_emits_no_rejects(self, db):
        metrics = RecordingMetrics()
        svc = _service(db, metrics=metrics)

        run_id = asyncio.run(svc.submit_async(_mk_req("t1")))

        assert svc._run_repo.get(dm.RunId(run_id), dm.TenantId("t1")) is not None
        assert metrics.admission_rejects() == []

    def test_llm_config_error_precedes_admission(self, db):
        run_repo = RunRepository(db)
        run_repo.create(_mk_running_run("t1"))
        cfg = AgentComputeConfig(
            llm=None,
            tenant_limits=AgentComputeTenantLimits(max_concurrent_runs=1),
            admission=AgentComputeAdmissionConfig(
                max_global_concurrent_runs=1, max_pending_jobs=1,
            ),
        )
        svc = AgentComputeService(
            run_repo=run_repo, job_repo=JobRepository(db),
            agent_repo=AgentRepository(db),
            message_repo=MessageRepository(db),
            round_repo=RoundRepository(db),
            guard_repo=GuardEventRepository(db),
            shard_repo=ResultShardRepository(db), config=cfg,
        )

        # Fail-clear config rejection keeps precedence over 429-class caps.
        with pytest.raises(AgentComputeConfigError):
            asyncio.run(svc.submit_async(_mk_req("t1")))