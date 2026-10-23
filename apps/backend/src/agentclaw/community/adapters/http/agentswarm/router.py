"""agentswarm HTTP adapter — thin FastAPI router + DTO conversion.

Authentication (D25): every public route resolves an authenticated
end-user through ``CurrentUserDep`` — an anonymous caller is refused
401 before any route logic (the read/stream/report routes enforce it
explicitly; unauthenticated reads were the regression this contract
now pins, since the explicit-tenant swap removed the transitive
principal dependency that used to carry the guard). ``/health`` and
``/ready`` stay auth-free (liveness probes).

Tenant scope: an explicit, per-request API parameter (``SubmitRunDTO.tenant``
on submit, ``TenantQuery`` everywhere else) — the authenticated caller
names the tenant the run belongs to; no shipped auth profile provides a
tenant through the principal (BCS: user_id only), so the parameter is
the single source of tenancy here. A blank tenant is 422-closed at
validation; no fallback resolves one. Cross-tenant access is refused
at the data layer (repo scoping), so it is indistinguishable from an
unknown run: the same 404 both ways, no "forbidden" oracle.

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
import dataclasses
import html
import json
from agentclaw.community.log import get_logger
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from agentclaw.community.api.agentswarm_service import (
    AgentSwarmServiceProtocol, SubmitRunRequest,
)
from agentclaw.community.core.agentswarm.domain.models import (
    AgentSpec, ProviderKind, RunStatus,
)
from agentclaw.community.core.agentswarm.domain.models import RunOptions
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentAuthorizationState, AgentSwarmConfigError, AgentSwarmRunNotFoundError,
    GlobalRunLimitExceeded, QueueDepthExceeded, TenantRunLimitExceeded,
)
from agentclaw.community.core.agentswarm.repository.audit.impl import AgentSwarmAuditWriter, AuditAction
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.agentswarm.schemas import (
    AgentCatalogSpecDTO, AgentDetailDTO, AgentMessageDetailDTO,
    AgentResultDetailDTO, AgentAttemptDetailDTO, BotAuthorizationDTO,
    CancelResponseDTO, GuardEventDetailDTO, RoundDetailDTO, RunDetailDTO,
    RunListDTO, RunSummaryDTO, StreamEventDTO, SubmitRunDTO,
    SubmitRunResponseDTO,
)
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser

logger = get_logger("agentswarm")

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

# The tenant scope is an explicit, per-request API parameter on this
# surface: the authenticated caller names the tenant the run belongs to,
# fail-closed 400 when absent. No shipped auth profile provides a tenant
# through the principal (BCS: user_id only), so the parameter is the single
# source of tenancy here; cross-tenant access stays refused at the data
# layer (repo scoping, D25 unchanged).
TenantQuery = Annotated[
    str,
    Query(min_length=1, max_length=64, description="tenant scope for the run"),
]


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


# Bot lifecycle states that matter to the SSE gate stream: the park state
# (D2) plus the authorization outcomes that resolve it. Everything else is
# ordinary provisioning drift and must NOT emit a gate event (D7 fast path).
_GATE_STATE = "AWAITING_AUTHORIZATION"


def authorization_gate_deltas(
    last: dict[str, str],
    snapshot: tuple[AgentAuthorizationState, ...],
) -> list[dict]:
    """Per-stream authorization gate crossings for one SSE poll.

    ``last`` is this connection's previous bot state per agent (absent =
    not yet seen by this stream). A delta is emitted when an agent enters
    the human gate from any other state (including "not seen" — a stream
    that attaches mid-park surfaces the pending consent once, URLs
    included) or when its state changes out of the gate (issued resumes
    provisioning; rejected/expired report the terminal auth state).
    Unchanged polls — and every non-gate transition such as the
    auto-issued PROVISIONING→READY path — emit nothing. Emissions ride the
    existing ``agent`` event kind with the same payload shape plus the bot
    fields (``bot_id``, ``bot_state``, and ``authorization`` while parked),
    so no new event vocabulary enters the wire contract.
    """
    deltas: list[dict] = []
    for item in snapshot:
        prev = last.get(item.agent_id)
        entered = (
            item.bot_state == _GATE_STATE and prev != _GATE_STATE
        )
        exited = prev == _GATE_STATE and item.bot_state != _GATE_STATE
        if not (entered or exited):
            continue
        payload: dict = {
            "agent_id": item.agent_id,
            "name": item.name,
            "status": item.status,
            "kind": item.kind,
            "spawn_round": item.spawn_round,
            "bot_id": item.bot_id,
            "bot_state": item.bot_state,
        }
        if item.authorization is not None:
            payload["authorization"] = {
                "state": item.authorization.state,
                "iframe_url": item.authorization.iframe_url,
                "redirect_url": item.authorization.redirect_url,
                "deadline": item.authorization.deadline,
            }
        deltas.append(payload)
    return deltas


@router.post("/runs")
async def submit_run(
    dto: SubmitRunDTO,
    svc: AgentSwarmServiceDep,
    user: CurrentUserDep,
    audit_writer: AgentSwarmAuditWriterDep,
    wait: bool = Query(False, description="block until terminal and return the full run"),
) -> SubmitRunResponseDTO | RunSummaryDTO:
    tenant = (dto.tenant or "").strip()
    req = _to_domain(dto, user.staffId)
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
    tenant: TenantQuery,
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
    tenant: TenantQuery,
    _user: CurrentUserDep,
) -> RunSummaryDTO:
    run = svc.get_run(run_id, tenant)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _to_summary(run)


@router.get("/runs")
async def list_runs(
    svc: AgentSwarmServiceDep,
    tenant: TenantQuery,
    _user: CurrentUserDep,
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


@router.get("/runs/{run_id}/detail")
async def run_detail(
    run_id: str,
    svc: AgentSwarmServiceDep,
    tenant: TenantQuery,
    _user: CurrentUserDep,
) -> RunDetailDTO:
    from agentclaw.community.core.agentswarm.service.protocols import RunDetail

    detail: RunDetail | None = svc.get_run_detail(run_id, tenant)
    if detail is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _to_detail_dto(detail)


@router.get("/runs/{run_id}/events")
async def stream_run(
    run_id: str,
    svc: AgentSwarmServiceDep,
    tenant: TenantQuery,
    _user: CurrentUserDep,
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
        # Per-stream view of each agent's bot state for the authorization
        # gate deltas: absent means this connection has not seen the agent
        # parked yet, so a stream attaching mid-park surfaces the gate
        # once. Not a row cursor — the durable cursors below stay the
        # only replay mechanism (each event once per stream).
        gate_seen: dict[str, str] = {}
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
            for delta in authorization_gate_deltas(
                    gate_seen, events.agent_authorizations):
                yield _sse("agent", delta)
            for item in events.agent_authorizations:
                gate_seen[item.agent_id] = item.bot_state
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
    tenant: TenantQuery,
    _user: CurrentUserDep,
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


def _to_domain(dto: SubmitRunDTO, owner_id: str = "") -> SubmitRunRequest:
    opts = RunOptions.from_dto(dto.options)
    if dto.model is not None:
        # The run-level ``model`` API parameter is the explicit
        # chat-completion model override; it outranks an options.model_name.
        opts = dataclasses.replace(opts, model_name=dto.model)
    return SubmitRunRequest(
        tenant=(dto.tenant or "").strip(),
        goal=dto.goal,
        agents=tuple(
            AgentSpec(
                name=a.name, role=a.role, instructions=a.instructions,
                kind=a.kind, model=a.model,
                engine=a.engine or "",
                bot_type=a.bot_type or "personal",
                identity_file=a.identity_file or "SOUL.md",
                metadata=a.metadata,
                config_manifest=a.config_manifest,
            )
            for a in dto.agents
        ),
        provider=ProviderKind.coerce(dto.provider),
        idempotency_key=dto.idempotency_key,
        owner_id=owner_id,
        run_options=opts,
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


def _to_detail_dto(detail) -> RunDetailDTO:
    from agentclaw.community.adapters.http.agentswarm.schemas import (
        AgentAttemptDetailDTO, AgentMessageDetailDTO,
    )
    return RunDetailDTO(
        run_id=detail.run_id,
        status=detail.status,
        termination_reason=detail.termination_reason,
        final_answer=detail.final_answer,
        error=detail.error,
        started_at=detail.started_at,
        ended_at=detail.ended_at,
        tenant=detail.tenant,
        goal=detail.goal,
        provider=detail.provider,
        agent_catalog=[
            AgentCatalogSpecDTO(
                name=spec.name,
                role=spec.role,
                kind=spec.kind.value,
                model=spec.model,
                engine=spec.engine,
            )
            for spec in detail.agent_catalog
        ],
        agents=[
            AgentDetailDTO(
                agent_id=a.agent_id,
                name=a.name,
                role=a.role,
                kind=a.kind,
                status=a.status,
                parent_agent_id=a.parent_agent_id,
                depth=a.depth,
                spawn_round=a.spawn_round,
                idempotency_key=a.idempotency_key,
                attempts=a.attempts,
                bot_id=a.bot_id,
                bot_state=a.bot_state,
                error=a.error,
                output=a.output,
                started_at=a.started_at,
                ended_at=a.ended_at,
                authorization=(
                    BotAuthorizationDTO(
                        state=a.authorization.state,
                        iframe_url=a.authorization.iframe_url,
                        redirect_url=a.authorization.redirect_url,
                        deadline=a.authorization.deadline,
                    )
                    if a.authorization is not None else None
                ),
                result=AgentResultDetailDTO(
                    payload=a.result.payload,
                    artifact_key=a.result.artifact_key,
                    routed_at=a.result.routed_at,
                ) if a.result is not None else None,
                attempt_ledger=[
                    AgentAttemptDetailDTO(
                        claim_epoch=t.claim_epoch,
                        slice_retry_index=t.slice_retry_index,
                        executor_kind=t.executor_kind,
                        executor_id=t.executor_id,
                        executor_name=t.executor_name,
                        binding_state=t.binding_state,
                        result_status=t.result_status,
                        result_message=t.result_message,
                        started_at=t.started_at,
                    )
                    for t in a.attempt_ledger
                ],
                messages=[
                    AgentMessageDetailDTO(
                        message_id=m.message_id,
                        agent_id=m.agent_id,
                        seq=m.seq,
                        role=m.role,
                        content=m.content,
                        decision=m.decision,
                        token_usage=m.token_usage,
                        artifact_key=m.artifact_key,
                        created_at=m.created_at,
                    )
                    for m in a.messages
                ],
            )
            for a in detail.agents
        ],
        rounds=[
            RoundDetailDTO(
                round_id=r.round_id,
                stage_index=r.stage_index,
                orchestrator_steps=r.orchestrator_steps,
                slowest_attempt_steps=r.slowest_attempt_steps,
                cohort_agent_ids=list(r.cohort_agent_ids),
                started_at=r.started_at,
                ended_at=r.ended_at,
            )
            for r in detail.rounds
        ],
        guards=[
            GuardEventDetailDTO(
                event_id=g.event_id,
                agent_id=g.agent_id,
                kind=g.kind,
                details=g.details,
                created_at=g.created_at,
            )
            for g in detail.guards
        ],
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