"""agentcompute concrete service — runs the swarm job lifecycle."""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from agentclaw.community.core.agentcompute.config.settings import AgentComputeConfig
from agentclaw.community.core.agentcompute.domain.models import (
    EpochMillis, ProviderId, Run, RunId, RunPage, RunStatus, TenantId,
)
from agentclaw.community.core.agentcompute.repository.impl import (
    AgentRepository, GuardEventRepository, JobRepository, RoundRepository,
    RunRepository,
)
from agentclaw.community.core.agentcompute.service_protocol import (
    AgentComputeConfigError, AgentComputeRunNotFoundError,
    AgentComputeServiceProtocol, AgentEvent, GlobalRunLimitExceeded,
    GuardEventSummary, QueueDepthExceeded, RoundEvent, RunEvents,
    SubmitRunRequest, TenantRunLimitExceeded,
)
from agentclaw.community.plugin_api.metrics import MetricsPlugin


class _NullMetrics:
    """Private no-op MetricsPlugin — submits run unmeasured when no
    metrics backend is configured (local/test profiles)."""

    def increment(self, name: str, labels: Any = None) -> None:
        return None

    def observe(self, name: str, value: float, labels: Any = None) -> None:
        return None

    def set_gauge(self, name: str, value: float, labels: Any = None) -> None:
        return None

    @contextmanager
    def time_block(self, name: str, labels: Any = None) -> Iterator[None]:
        yield


class AgentComputeService(AgentComputeServiceProtocol):

    def __init__(self, run_repo: RunRepository, job_repo: JobRepository,
                 agent_repo: AgentRepository, round_repo: RoundRepository,
                 guard_repo: GuardEventRepository,
                 config: AgentComputeConfig,
                 metrics: MetricsPlugin | None = None) -> None:
        self._run_repo = run_repo
        self._job_repo = job_repo
        self._agent_repo = agent_repo
        self._round_repo = round_repo
        self._guard_repo = guard_repo
        self._config = config
        self._metrics: MetricsPlugin = metrics or _NullMetrics()
        slice_max = config.orchestration.slice_max_seconds
        self._deadline = int(slice_max) if slice_max else 3600

    async def submit_async(self, req: SubmitRunRequest) -> str:
        self._require_llm_config()
        if req.idempotency_key:
            existing = self._run_repo.get_by_idempotency(
                req.idempotency_key, TenantId(req.tenant),
            )
            if existing is not None:
                # Admission (12.1) is checked after the idempotency join:
                # a duplicate submit joins an existing run and consumes no
                # new capacity, so saturation must never refuse it.
                return existing.run_id
        limit = self._config.tenant_limits.max_concurrent_runs
        if self._run_repo.count_active_runs(TenantId(req.tenant)) >= limit:
            self._metrics.increment(
                "agentcompute_admission_reject", {"reason": "tenant"},
            )
            raise TenantRunLimitExceeded(tenant=req.tenant, limit=limit)
        global_limit = self._config.admission.max_global_concurrent_runs
        if self._run_repo.count_active_runs_global() >= global_limit:
            self._metrics.increment(
                "agentcompute_admission_reject", {"reason": "global"},
            )
            raise GlobalRunLimitExceeded(limit=global_limit)
        pending_limit = self._config.admission.max_pending_jobs
        if self._job_repo.count_pending_jobs() >= pending_limit:
            self._metrics.increment(
                "agentcompute_admission_reject", {"reason": "queue"},
            )
            raise QueueDepthExceeded(limit=pending_limit)
        run_id = uuid.uuid4().hex
        run = Run(
            run_id=RunId(run_id),
            tenant=TenantId(req.tenant),
            goal=req.goal,
            agent_catalog=req.agents,
            provider=ProviderId(req.provider),
            request_metadata=req.request_metadata,
            status=RunStatus.RUNNING,
            started_at=EpochMillis(int(time.time() * 1000)),
        )
        run.active_idempotency_key = req.idempotency_key
        self._run_repo.create(run)
        self._job_repo.enqueue(
            run_id=RunId(run_id),
            tenant=TenantId(req.tenant),
            deadline_seconds=self._deadline,
        )
        return run_id

    async def submit_sync(self, req: SubmitRunRequest) -> Run:
        run_id = await self.submit_async(req)
        deadline = time.monotonic() + self._deadline
        while time.monotonic() < deadline:
            run = self.get_run(run_id, req.tenant)
            if run is not None and run.status is not RunStatus.RUNNING:
                return run
            await asyncio.sleep(1.0)
        return self.get_run(run_id, req.tenant) or Run(
            run_id=RunId(run_id), tenant=TenantId(req.tenant), goal=req.goal,
            agent_catalog=req.agents, provider=ProviderId(req.provider),
            request_metadata=req.request_metadata, status=RunStatus.TIMED_OUT,
            started_at=EpochMillis(0),
        )

    def get_run(self, run_id: str, tenant: str) -> Run | None:
        return self._run_repo.get(RunId(run_id), TenantId(tenant))

    def list_runs(self, tenant: str, status: RunStatus | None = None,
                  *, page: int = 1, page_size: int = 20) -> RunPage:
        return self._run_repo.list_page(
            TenantId(tenant), status, page, page_size,
        )

    async def cancel(self, run_id: str, tenant: str) -> str:
        ok = self._job_repo.request_cancel(RunId(run_id), TenantId(tenant))
        if not ok:
            return "not_found"
        run = self.get_run(run_id, tenant)
        if run is not None and run.status is not RunStatus.RUNNING:
            return run.status.value
        return RunStatus.RUNNING.value

    def run_events(self, run_id: str, tenant: str, after_agent_id: int = 0,
                   after_round_id: int = 0, after_event: int = 0) -> RunEvents:
        rid, tid = RunId(run_id), TenantId(tenant)
        run = self._run_repo.get(rid, tid)
        if run is None:
            raise AgentComputeRunNotFoundError(run_id)
        agents = tuple(
            AgentEvent(
                agent_id=a.agent_id, name=a.name, status=a.status, kind=a.kind,
                spawn_round=a.spawn_round, row_cursor=row,
            )
            for row, a in self._agent_repo.list_by_run_with_rows(rid, tid)
            if row > after_agent_id
        )
        rounds = tuple(
            RoundEvent(
                round_id=r.id, stage_index=r.stage_index,
                cohort_size=len(r.cohort_agent_ids), row_cursor=row,
            )
            for row, r in self._round_repo.list_by_run_with_rows(rid, tid)
            if row > after_round_id
        )
        guards = tuple(
            GuardEventSummary(kind=g.kind, row_cursor=row)
            for row, g in self._guard_repo.list_by_run_with_rows(rid, tid)
            if row > after_event
        )
        terminal = run.status if run.status is not RunStatus.RUNNING else None
        return RunEvents(
            agents=agents, rounds=rounds, guards=guards,
            run_status=run.status, terminal=terminal,
        )

    def _require_llm_config(self) -> None:
        llm = self._config.llm
        if llm is None or not llm.base_url:
            raise AgentComputeConfigError(
                "agentcompute llm config missing: base_url/api_key required"
            )