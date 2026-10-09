from __future__ import annotations

import time
import uuid
from typing import Any

from agentclaw.community.api.agentcompute.service_protocol import (
    SubmitRunRequest,
)
from agentclaw.community.core.agentcompute.domain.models import (
    EpochMillis, ProviderId, RequestMetadata, Run, RunId, RunPage,
    RunStatus, TenantId, TerminationReason,
)
from agentclaw.community.core.agentcompute.repository.impl import (
    RunRepository, JobRepository,
)


class AgentComputeService:

    def __init__(self, run_repo: RunRepository, job_repo: JobRepository,
                 deadline_seconds: int = 3600) -> None:
        self._run_repo = run_repo
        self._job_repo = job_repo
        self._deadline = deadline_seconds

    async def submit_async(self, req: SubmitRunRequest) -> str:
        existing = None
        if req.idempotency_key:
            existing = self._run_repo.get_by_idempotency(req.idempotency_key, req.tenant)
            if existing is not None:
                return existing.run_id

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
        import asyncio
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

    def list_runs(self, tenant: str, status: str | None = None,
                  *, page: int = 1, page_size: int = 20) -> RunPage:
        return RunPage(items=(), total=0, page=page, page_size=page_size)

    async def cancel(self, run_id: str, tenant: str) -> str:
        ok = self._job_repo.request_cancel(RunId(run_id), TenantId(tenant))
        if not ok:
            return "not_found"
        run = self.get_run(run_id, tenant)
        if run is not None and run.status is not RunStatus.RUNNING:
            return run.status.value
        return RunStatus.RUNNING.value