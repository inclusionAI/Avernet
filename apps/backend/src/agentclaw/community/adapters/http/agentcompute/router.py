"""agentcompute HTTP adapter — thin FastAPI router + DTO conversion."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from agentclaw.community.api.agentcompute_service import (
    AgentComputeServiceProtocol, SubmitRunRequest,
)
from agentclaw.community.core.agentcompute.domain.models import (
    AgentSpecRaw, RunStatus,
)
from agentclaw.community.core.agentcompute.service_protocol import (
    AgentComputeConfigError, AgentComputeRunNotFoundError, TenantRunLimitExceeded,
)
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.agentcompute.schemas import (
    CancelResponseDTO, RunListDTO, RunSummaryDTO,
    StreamEventDTO, SubmitRunDTO, SubmitRunResponseDTO,
)

logger = logging.getLogger(__name__)

AgentComputeServiceDep = Annotated[
    AgentComputeServiceProtocol,
    Injected(AgentComputeServiceProtocol),
]

router = APIRouter(prefix="/agentcompute/v1", tags=["agentcompute"])


@router.post("/runs")
async def submit_run(
    dto: SubmitRunDTO,
    request: Request,
    svc: AgentComputeServiceDep,
    wait: bool = Query(False, description="block until terminal and return the full run"),
) -> SubmitRunResponseDTO | RunSummaryDTO:
    req = _to_domain(dto)
    if wait:
        return _to_summary(await svc.submit_sync(req))
    return SubmitRunResponseDTO(run_id=await svc.submit_async(req))


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    request: Request,
    svc: AgentComputeServiceDep,
) -> RunSummaryDTO:
    tenant = _get_tenant(request)
    run = svc.get_run(run_id, tenant)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _to_summary(run)


@router.get("/runs")
async def list_runs(
    request: Request,
    svc: AgentComputeServiceDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: RunStatus | None = Query(None),
):
    tenant = _get_tenant(request)
    rp = svc.list_runs(tenant, status, page=page, page_size=page_size)
    return RunListDTO(
        items=[_to_summary(r) for r in rp.items],
        total=rp.total,
        page=rp.page,
        page_size=rp.page_size,
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    request: Request,
    svc: AgentComputeServiceDep,
) -> CancelResponseDTO:
    tenant = _get_tenant(request)
    status = await svc.cancel(run_id, tenant)
    if status == "not_found":
        raise HTTPException(status_code=404, detail="run not found")
    return CancelResponseDTO(run_id=run_id, status=status)


@router.get("/runs/{run_id}/events")
async def stream_run(
    run_id: str,
    request: Request,
    svc: AgentComputeServiceDep,
) -> StreamingResponse:
    tenant = _get_tenant(request)

    def _sse(event: str, data: dict) -> str:
        evt = StreamEventDTO(event=event, run_id=run_id, data=data)
        return f"event: {evt.event}\ndata: {evt.model_dump_json()}\n\n"

    async def generate():
        run = svc.get_run(run_id, tenant)
        if run is None:
            yield "event: error\ndata: " + json.dumps({"error": "not found"}) + "\n\n"
            return
        yield _sse("status", {"status": run.status.value})
        after_agent = after_round = after_event = 0
        while True:
            try:
                events = svc.run_events(
                    run_id, tenant, after_agent, after_round, after_event,
                )
            except AgentComputeRunNotFoundError:
                break
            for agent in events.agents:
                yield _sse("agent", {
                    "agent_id": agent.agent_id,
                    "name": agent.name,
                    "status": agent.status.value,
                    "kind": agent.kind.value,
                    "spawn_round": agent.spawn_round,
                })
                after_agent = agent.row_cursor
            for round_event in events.rounds:
                yield _sse("round", {
                    "round_id": round_event.round_id,
                    "stage_index": round_event.stage_index,
                    "cohort_size": round_event.cohort_size,
                })
                after_round = round_event.row_cursor
            for guard in events.guards:
                yield _sse("guard", {"kind": guard.kind.value})
                after_event = guard.row_cursor
            if events.terminal is not None:
                terminal = StreamEventDTO(
                    event="terminal", run_id=run_id,
                    data={"status": events.terminal.value},
                )
                yield (
                    "event: terminal\nid: terminal\nretry: 5000\n"
                    f"data: {terminal.model_dump_json()}\n\n"
                )
                break
            yield _sse("progress", {"status": events.run_status.value})
            await asyncio.sleep(1.0)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/runs/{run_id}/report", response_class=HTMLResponse)
async def report(
    run_id: str,
    request: Request,
    svc: AgentComputeServiceDep,
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


def register_exception_handlers(app: FastAPI) -> FastAPI:
    app.add_exception_handler(TenantRunLimitExceeded, _tenant_limit_handler)
    app.add_exception_handler(AgentComputeConfigError, _config_error_handler)
    return app


def _tenant_limit_handler(request: Request, exc: TenantRunLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "error": "tenant_run_limit_exceeded",
            "tenant": exc.tenant,
            "limit": exc.limit,
        },
    )


def _config_error_handler(request: Request, exc: AgentComputeConfigError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": str(exc)})


def _get_tenant(request: Request) -> str:
    tenant = request.headers.get("X-Tenant-Id", "")
    if not tenant:
        raise HTTPException(status_code=400, detail="X-Tenant-Id required")
    return tenant


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