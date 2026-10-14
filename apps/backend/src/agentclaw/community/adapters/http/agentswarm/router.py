"""agentswarm HTTP adapter — thin FastAPI router + DTO conversion.

Stage B (tasks 12.7/12.8), hardened (P0-3): tenant identity resolves
from the authenticated principal ONLY
(:class:`AuthenticatedUser.tenantId`, D25 — the caller proves who they
are; the request body and headers never supply a tenant). The former
``X-Tenant-Id`` header fallback is removed: no shipped profile
(``OidcAuthPlugin``, local auth) ever populated ``tntInstId``, so the
header was the effective tenant for every authenticated user — a
client-controlled tenant means cross-tenant read/stream/report/cancel/
submit. A principal that carries no tenant now fails closed with 400
``{"detail": "tenant unresolvable"}``; the client-supplied header is
never read. Cross-tenant access is refused at the data layer (repo
scoping), so it is indistinguishable from an unknown run: the same 404
both ways, no "forbidden" oracle.

Auditing (``ac_agent_swarm_audit``, append-only, best-effort): accepted
and rejected submits with admission reasons (``tenant_limit`` /
``global_limit`` / ``queue_depth`` / ``config_missing``), and cancel
outcomes. Route handlers run after the audit seam, so audit failures
can never change a response — :func:`_audit` swallows, and the writer
swallows again.

Versioning (12.8): this surface is ``/openapi/v1/agentswarm`` — additive-only
compatibility within ``v1``; agent/field renames or removals require a new
URL prefix (``/openapi/v2/agentswarm``) — see the version policy in the module
README. ``API_VERSION`` below is the machine-read keyword: it surfaces in
the health payload so operators can pin a fleet against drift.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from agentclaw.community.api.agentswarm_service import (
    AgentSwarmServiceProtocol, SubmitRunRequest,
)
from agentclaw.community.core.agentswarm.domain.models import (
    AgentSpec, ProviderKind, RunStatus,
)
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentSwarmConfigError, AgentSwarmRunNotFoundError,
    GlobalRunLimitExceeded, QueueDepthExceeded, TenantRunLimitExceeded,
)
from agentclaw.community.core.agentswarm.repository.audit.impl import AgentSwarmAuditWriter, AuditAction
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.agentswarm.schemas import (
    CancelResponseDTO, RunListDTO, RunSummaryDTO,
    StreamEventDTO, SubmitRunDTO, SubmitRunResponseDTO,
)
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser

logger = logging.getLogger(__name__)

API_VERSION = "v1"

AgentSwarmServiceDep = Annotated[
    AgentSwarmServiceProtocol,
    Injected(AgentSwarmServiceProtocol),
]
AgentSwarmAuditWriterDep = Annotated[
    AgentSwarmAuditWriter,
    Injected(AgentSwarmAuditWriter),
]
CurrentUserDep = Annotated[AuthenticatedUser, Depends(get_current_user)]

router = APIRouter(prefix="/openapi/v1/agentswarm", tags=["agentswarm"])


def _resolve_tenant(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> str:
    """Effective tenant for this request — principal ONLY, fail closed.

    D25: the authenticated principal is the tenant authority. No shipped
    auth profile populated ``tntInstId`` when the ``X-Tenant-Id`` header
    fallback existed, which made the client-controlled header the
    effective tenant for every authenticated caller (P0-3) — so the
    fallback is removed and this boundary never reads a client-supplied
    tenant from any request attribute. A principal that carries no
    tenant fails closed with 400.

    A tenant that resolves to nothing is 400 rather than 401: the caller
    may well be authenticated — their principal simply carries no tenant —
    so "credentials" would be the wrong fix, and the precise message says
    only the identity provider can produce one.
    """
    tenant = (user.tenantId or "").strip()
    if not tenant:
        raise HTTPException(
            status_code=400,
            detail="tenant unresolvable",
        )
    return tenant


TenantDep = Annotated[str, Depends(_resolve_tenant)]


def _audit(
    writer: AgentSwarmAuditWriter,
    *,
    tenant: str,
    actor: str,
    action: AuditAction,
    reason: str | None = None,
    run_id: str | None = None,
) -> None:
    """Best-effort boundary audit — layered after the writer's own guard.

    One failure is swallowed inside the writer (DB down, constraint);
    a second here catches a writer that is raises-broken by construction,
    so an audit bug can never 5xx or 4xx the caller's request.
    """
    try:
        writer.record(
            tenant=tenant, actor=actor, action=action,
            reason=reason, run_id=run_id,
        )
    except Exception:
        logger.exception(
            "[AgentSwarmAudit] audit call dropped (action=%s run_id=%s tenant=%s)",
            action, run_id, tenant,
        )


@router.post("/runs")
async def submit_run(
    dto: SubmitRunDTO,
    svc: AgentSwarmServiceDep,
    tenant: TenantDep,
    user: CurrentUserDep,
    audit_writer: AgentSwarmAuditWriterDep,
    wait: bool = Query(False, description="block until terminal and return the full run"),
) -> SubmitRunResponseDTO | RunSummaryDTO:
    req = _to_domain(dto, tenant)
    try:
        if wait:
            run = await svc.submit_sync(req)
            summary: SubmitRunResponseDTO | RunSummaryDTO = _to_summary(run)
            run_id = run.run_id
        else:
            run_id = await svc.submit_async(req)
            summary = SubmitRunResponseDTO(run_id=run_id)
    except TenantRunLimitExceeded:
        _audit(audit_writer, tenant=tenant, actor=user.staffId,
               action=AuditAction.SUBMIT_REJECTED, reason="tenant_limit")
        raise
    except GlobalRunLimitExceeded:
        _audit(audit_writer, tenant=tenant, actor=user.staffId,
               action=AuditAction.SUBMIT_REJECTED, reason="global_limit")
        raise
    except QueueDepthExceeded:
        _audit(audit_writer, tenant=tenant, actor=user.staffId,
               action=AuditAction.SUBMIT_REJECTED, reason="queue_depth")
        raise
    except AgentSwarmConfigError:
        _audit(audit_writer, tenant=tenant, actor=user.staffId,
               action=AuditAction.SUBMIT_REJECTED, reason="config_missing")
        raise
    _audit(audit_writer, tenant=tenant, actor=user.staffId,
           action=AuditAction.SUBMIT_ACCEPTED, run_id=run_id)
    return summary


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    svc: AgentSwarmServiceDep,
    tenant: TenantDep,
    user: CurrentUserDep,
    audit_writer: AgentSwarmAuditWriterDep,
) -> CancelResponseDTO:
    status = await svc.cancel(run_id, tenant)
    if status == "not_found":
        _audit(audit_writer, tenant=tenant, actor=user.staffId,
               action=AuditAction.CANCEL_REQUESTED, reason="not_found")
        raise HTTPException(status_code=404, detail="run not found")
    _audit(audit_writer, tenant=tenant, actor=user.staffId,
           action=AuditAction.CANCEL_REQUESTED, run_id=run_id)
    return CancelResponseDTO(run_id=run_id, status=status)


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    svc: AgentSwarmServiceDep,
    tenant: TenantDep,
) -> RunSummaryDTO:
    run = svc.get_run(run_id, tenant)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _to_summary(run)


@router.get("/runs")
async def list_runs(
    svc: AgentSwarmServiceDep,
    tenant: TenantDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: RunStatus | None = Query(None),
):
    rp = svc.list_runs(tenant, status, page=page, page_size=page_size)
    return RunListDTO(
        items=[_to_summary(r) for r in rp.items],
        total=rp.total,
        page=rp.page,
        page_size=rp.page_size,
    )


@router.get("/runs/{run_id}/events")
async def stream_run(
    run_id: str,
    svc: AgentSwarmServiceDep,
    tenant: TenantDep,
) -> StreamingResponse:
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
            except AgentSwarmRunNotFoundError:
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
    svc: AgentSwarmServiceDep,
    tenant: TenantDep,
) -> HTMLResponse:
    run = svc.get_run(run_id, tenant)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return HTMLResponse(
        _render_report(run),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": API_VERSION}


@router.get("/ready")
async def ready() -> dict[str, bool]:
    return {"ready": True}


def register_exception_handlers(app: FastAPI) -> FastAPI:
    app.add_exception_handler(TenantRunLimitExceeded, _tenant_limit_handler)
    app.add_exception_handler(GlobalRunLimitExceeded, _global_limit_handler)
    app.add_exception_handler(QueueDepthExceeded, _queue_depth_handler)
    app.add_exception_handler(AgentSwarmConfigError, _config_error_handler)
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


def _global_limit_handler(request: Request, exc: GlobalRunLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "error": "global_run_limit_exceeded",
            "limit": exc.limit,
        },
    )


def _queue_depth_handler(request: Request, exc: QueueDepthExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "error": "queue_depth_exceeded",
            "limit": exc.limit,
        },
    )


def _config_error_handler(request: Request, exc: AgentSwarmConfigError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": str(exc)})


def _to_domain(dto: SubmitRunDTO, tenant: str) -> SubmitRunRequest:
    return SubmitRunRequest(
        tenant=tenant,
        goal=dto.goal,
        agents=tuple(
            AgentSpec(
                name=a.name, role=a.role, instructions=a.instructions,
                kind=a.kind, model=a.model,
                engine=a.engine or "",
                bot_type=a.bot_type or "personal",
                identity_file=a.identity_file or "SOUL.md",
                metadata=a.metadata,
            )
            for a in dto.agents
        ),
        provider=ProviderKind.coerce(dto.provider),
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
    # P0-5 (stored XSS): interpolate only html-escaped dynamic values —
    # even server-generated-looking ones (ids, statuses) flow through
    # persisted rows, so nothing raw reaches the document.
    run_id = html.escape(str(run.run_id))
    goal = html.escape(str(run.goal))
    status = html.escape(run.status.value)
    termination = html.escape(
        run.termination_reason.value if run.termination_reason else "—"
    )
    status_class = "ok" if run.status is RunStatus.COMPLETED else "err"
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>Agent Swarm — Run Report</title>
<style>body{{font-family:system-ui;background:#10151c;color:#e6edf3;margin:20px;}}
h1{{font-size:18px;}} .meta{{color:#9aa4b2;}} .ok{{color:#4caf50;font-weight:bold;}} .err{{color:#e53935;font-weight:bold;}}</style>
</head><body>
<h1>Agent Swarm — Run Report</h1>
<div class="meta">Run ID: {run_id}</div>
<div class="meta">Goal: {goal}</div>
<div class="meta">Status: <span class="{status_class}">{status}</span></div>
<div class="meta">Termination: {termination}</div>
<div class="meta">Agents spawned: {len(run.agent_catalog)}</div>
</body></html>"""