"""Human-authenticated task clarification and formal creation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors
from agentclaw.community.adapters.http.task.schemas_intake import (
    StartIntakeRequestDTO, TaskIntakeCommandDTO, TaskIntakeResultDTO,
    intake_result_to_dto,
)
from agentclaw.community.adapters.http.task.schemas import TaskOpResultDTO, op_result_to_dto
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.intake import TaskIntakeContext
from agentclaw.community.core.task.domain.models import TaskSourceType
from agentclaw.community.core.task.task_center.task_intake import build_task_info_request
from agentclaw.community.di import Injected

router = APIRouter()


@router.post("/intake", response_model=Envelope[TaskIntakeResultDTO])
@envelope_errors
async def intake_task(
    body: TaskIntakeCommandDTO,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    session_id: str | None = Header(default=None, alias="X-Task-Intake-Session-ID"),
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[TaskIntakeResultDTO]:
    context = TaskIntakeContext(
        session_id=session_id,
        owner_user_id=user.staffId,
        source_type=TaskSourceType.API,
    )
    result = await service.intake(body.to_domain(), context)
    return envelope(intake_result_to_dto(result), request)


@router.post("/start", response_model=Envelope[TaskOpResultDTO])
@envelope_errors
async def start_confirmed_task(
    body: StartIntakeRequestDTO,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[TaskOpResultDTO]:
    confirmed = service.get_confirmed_intake(body.session_id, user.staffId)
    result = await service.execute(build_task_info_request(confirmed))
    return envelope(op_result_to_dto(result), request)
