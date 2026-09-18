"""HTTP adapter routes for generic relay search/dispatch and BBS compatibility."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors
from agentclaw.community.adapters.http.task.auth import CallbackAuthenticator
from agentclaw.community.adapters.http.task.schemas import (
    BbsAttachDTO,
    BbsClaimDTO,
    BbsResultDTO,
    TaskDispatchRequestDTO,
    TaskSearchRequestDTO,
    acceptance_result_from_dto,
    task_spec_from_dto,
)
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.di import Injected

router = APIRouter()


@router.post("/search", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def search_task_candidates(
    body: TaskSearchRequestDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    raw = await request.body()
    auth.verify(
        source="task_loop", headers=request.headers, raw_body=raw,
        method=request.method, path=request.url.path,
    )
    result = await service.search_task_candidates(query=body.query)
    return envelope(result, request)


@router.post("/dispatch", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def dispatch_task(
    body: TaskDispatchRequestDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    raw = await request.body()
    auth.verify(
        source="task_loop", headers=request.headers, raw_body=raw,
        method=request.method, path=request.url.path,
    )
    result = await service.dispatch_task(
        task_id=body.task_id, node_id=body.node_id,
        holder_id=body.holder_id, relay_turn=body.relay_turn,
        dispatch_id=body.dispatch_id,
    )
    return envelope(result, request)


@router.post("/bbs/claim", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def bbs_claim(
    body: BbsClaimDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    # Relay claims carry node_id and use the same trusted task-loop channel as
    # report/search/dispatch. Legacy centralized root claims remain unchanged.
    if body.node_id is not None:
        raw = await request.body()
        auth.verify(
            source="task_loop", headers=request.headers, raw_body=raw,
            method=request.method, path=request.url.path,
        )
    result = service.claim_bbs_task(body.task_id, body.bot_id, body.node_id)
    return envelope({"root_node_id": result.node_id, "task_id": body.task_id}, request)


@router.post("/bbs/attach", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def bbs_attach(
    body: BbsAttachDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    node = service.attach_bbs_node(
        body.task_id, body.parent_node_id, task_spec_from_dto(body.task_spec), body.bot_id
    )
    return envelope({"node_id": node.node_id, "task_id": body.task_id}, request)


@router.post("/bbs/result", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def bbs_result(
    body: BbsResultDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    acceptance = (
        acceptance_result_from_dto(body.acceptance_result)
        if body.acceptance_result else None
    )
    await service.report_bbs_result(
        body.task_id, body.node_id, body.bot_id,
        acceptance_result=acceptance,
        output_patch=body.output_patch,
        exec_error=body.exec_error,
    )
    return envelope({"ok": True}, request)
