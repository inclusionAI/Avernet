"""Centralized BBS routes, intentionally separate from the Relay protocol."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors
from agentclaw.community.adapters.http.task.schemas import (
    BbsAttachDTO,
    BbsResultDTO,
    acceptance_result_from_dto,
    task_spec_from_dto,
)
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.di import Injected

router = APIRouter()


@router.post("/bbs/attach", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def bbs_attach(
    body: BbsAttachDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """Attach a centralized BBS work node; Relay uses PLAN_RESULT instead."""
    node = service.attach_bbs_node(
        body.task_id, body.parent_node_id, task_spec_from_dto(body.task_spec), body.bot_id,
    )
    return envelope({"node_id": node.node_id, "task_id": body.task_id}, request)


@router.post("/bbs/result", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def bbs_result(
    body: BbsResultDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """Report a centralized BBS result; Relay reports through /callback/report."""
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
