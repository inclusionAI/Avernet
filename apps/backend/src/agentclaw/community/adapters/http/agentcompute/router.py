from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from injector import Inject, inject

from agentclaw.community.api.agentcompute.service_protocol import (
    AgentComputeServiceProtocol, SubmitRunRequest,
)
from agentclaw.community.core.agentcompute.domain.models import (
    AgentSpecRaw, RequestMetadata, RunStatus, TenantId,
)
from agentclaw.community.adapters.http.agentcompute.schemas import (
    AgentSpecDTO, CancelResponseDTO, PageDTO, RunListDTO, RunSummaryDTO,
    StreamEventDTO, SubmitRunDTO, SubmitRunResponseDTO,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agentcompute/v1", tags=["agentcompute"])


@router.post("/run")
async def run_sync(
    dto: SubmitRunDTO,
    svc: Annotated[AgentComputeServiceProtocol, Depends(_get_service)],
) -> RunSummaryDTO:
    req = _to_domain(dto)
    run = await svc.submit_sync(req)
    return _to_summary(run)


@router.post("/jobs")
async def submit_job(
    dto: SubmitRunDTO,
    svc: Annotated[AgentComputeServiceProtocol, Depends(_get_service)],
) -> SubmitRunResponseDTO:
    req = _to_domain(dto)
    run_id = await svc.submit_async(req)
    return SubmitRunResponseDTO(run_id=run_id)


@router.get("/jobs/{run_id}")
async def get_run(
    run_id: str,
    request: Request,
    svc: Annotated[AgentComputeServiceProtocol, Depends(_get_service)],
) -> RunSummaryDTO:
    tenant = _get_tenant(request)
    run = svc.get_run(run_id, tenant)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _to_summary(run)


@router.get("/jobs")
async def list_runs(
    request: Request,
    svc: Annotated[AgentComputeServiceProtocol, Depends(_get_service)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
):
    tenant = _get_tenant(request)
    rp = svc.list_runs(tenant, status, page=page, page_size=page_size)
    return RunListDTO(
        items=[_to_summary(r) for r in rp.items],
        total=rp.total,
        page=rp.page,
        page_size=rp.page_size,
    )


@router.post("/jobs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    request: Request,
    svc: Annotated[AgentComputeServiceProtocol, Depends(_get_service)],
) -> CancelResponseDTO:
    tenant = _get_tenant(request)
    status = await svc.cancel(run_id, tenant)
    if status == "not_found":
        raise HTTPException(status_code=404, detail="run not found")
    return CancelResponseDTO(run_id=run_id, status=status)


@router.get("/jobs/{run_id}/stream")
async def stream_run(
    run_id: str,
    request: Request,
    svc: Annotated[AgentComputeServiceProtocol, Depends(_get_service)],
) -> StreamingResponse:
    tenant = _get_tenant(request)

    async def generate():
        run = svc.get_run(run_id, tenant)
        if run is None:
            yield f"event: error\ndata: {json.dumps({'error': 'not found'})}\n\n"
            return
        evt = StreamEventDTO(event="status", run_id=run_id, data={"status": run.status.value})
        yield f"event: {evt.event}\ndata: {evt.json()}\n\n"
        while True:
            run = svc.get_run(run_id, tenant)
            if run is None:
                break
            if run.status is not RunStatus.RUNNING:
                evt = StreamEventDTO(event="terminal", run_id=run_id, data={"status": run.status.value})
                yield f"event: {evt.event}\nid: terminal\nretry: 5000\ndata: {evt.json()}\n\n"
                break
            yield f"event: progress\ndata: {json.dumps({'status': run.status.value})}\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/jobs/{run_id}/report", response_class=HTMLResponse)
async def report(
    run_id: str,
    request: Request,
    svc: Annotated[AgentComputeServiceProtocol, Depends(_get_service)],
) -> str:
    tenant = _get_tenant(request)
    run = svc.get_run(run_id, tenant)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _render_report(run)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict[str, bool]:
    return {"ready": True}


def _get_tenant(request: Request) -> str:
    tenant = request.headers.get("X-Tenant-Id", "")
    if not tenant:
        raise HTTPException(status_code=400, detail="X-Tenant-Id required")
    return tenant


def _get_service(request: Request) -> AgentComputeServiceProtocol:
    return request.app.state.agentcompute_service


def _to_domain(dto: SubmitRunDTO) -> SubmitRunRequest:
    return SubmitRunRequest(
        tenant=dto.tenant,
        goal=dto.goal,
        agents=tuple(
            AgentSpecRaw(
                name=a.name, role=a.role, instructions=a.instructions,
                kind=a.kind, model=a.model, metadata=a.metadata,
            )
            for a in dto.agents
        ),
        provider=dto.provider or "default",
        model=dto.model,
        idempotency_key=dto.idempotency_key,
    )


def _to_summary(run) -> RunSummaryDTO:
    return RunSummaryDTO(
        run_id=run.run_id,
        status=run.status.value,
        termination_reason=run.termination_reason.value if run.termination_reason else None,
        final_answer=run.final_answer,
        error=run.error,
        started_at=run.started_at,
        ended_at=run.ended_at,
    )


def _render_report(run) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>Agent Compute — Run Report</title>
<style>body{{font-family:system-ui;background:#10151c;color:#e6edf3;margin:20px;}}
h1{{font-size:18px;}} .meta{{color:#9aa4b2;}} .ok{{color:#4caf50;font-weight:bold;}} .err{{color:#e53935;font-weight:bold;}}</style>
</head><body>
<h1>Agent Compute — Run Report</h1>
<div class="meta">Run ID: {run.run_id}</div>
<div class="meta">Goal: {run.goal}</div>
<div class="meta">Status: <span class="{'ok' if run.status is RunStatus.COMPLETED else 'err'}">{run.status.value}</span></div>
<div class="meta">Termination: {run.termination_reason.value if run.termination_reason else '—'}</div>
<div class="meta">Agents spawned: {len(run.agent_catalog)}</div>
</body></html>"""