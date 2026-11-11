"""Internal /api/v1 delivery for the context collab service (spec §6.1).

薄 delivery:装订(认证 → ActorContext)、Pydantic 校验与 ``to_domain``
命令组装、Envelope 序列化、领域错误交 ``@envelope_errors`` 映射
(``responses.ENVELOPE_ERRORS`` 的 4 行 ContextCollab 错误)。路由前缀是
spec §6.1 的单数 ``/api/v1/collaboration/task``,与既有内部
``/api/v1/collaboration/tasks`` 面区分。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.task.schemas_context_collab import (
    ColletCreateRequest,
    ContextCollabCreatedResultDto,
    ContextCollabRunResultDto,
    ContextCollabStatusResponse,
    EventAcceptedResultDto,
    EventCreateRequest,
    created_result_to_dto,
    event_accepted_to_dto,
    run_result_to_dto,
    status_to_dto,
)
from agentclaw.community.api.task.context_collab_service import (
    ContextCollabServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.service_api import ActorContext
from agentclaw.community.di import Injected

router = APIRouter(prefix="/api/v1/collaboration/task", tags=["task-context-collab"])


def _actor_context(user: AuthenticatedUser) -> ActorContext:
    """认证主体 → 服务调用上下文;G1 的 actor 一律取 ``user.staffId``。"""
    return ActorContext(actor=PrincipalRef("human", user.staffId), source="api")


@router.post("/context_collab", response_model=Envelope[ContextCollabCreatedResultDto])
@envelope_errors
async def create_context_collab(
    body: ColletCreateRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ContextCollabServiceProtocol = Injected(ContextCollabServiceProtocol),  # noqa: B008
) -> Envelope[ContextCollabCreatedResultDto]:
    result = service.create_context(body.to_domain(), _actor_context(user))
    return envelope(created_result_to_dto(result), request)


@router.post(
    "/context_collab/{collab_context_id}/events",
    response_model=Envelope[EventAcceptedResultDto],
)
@envelope_errors
async def append_context_collab_event(
    collab_context_id: str,
    body: EventCreateRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ContextCollabServiceProtocol = Injected(ContextCollabServiceProtocol),  # noqa: B008
) -> Envelope[EventAcceptedResultDto]:
    context = _actor_context(user)
    command = body.to_domain(context.actor)
    result = service.append_event(collab_context_id, command, context)
    return envelope(event_accepted_to_dto(result), request)


@router.post(
    "/context_collab/{collab_context_id}/run",
    response_model=Envelope[ContextCollabRunResultDto],
)
@envelope_errors
async def run_context_collab(
    collab_context_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ContextCollabServiceProtocol = Injected(ContextCollabServiceProtocol),  # noqa: B008
) -> Envelope[ContextCollabRunResultDto]:
    result = await service.run_context(collab_context_id, _actor_context(user))
    return envelope(run_result_to_dto(result), request)


@router.get(
    "/context_collab/{collab_context_id}",
    response_model=Envelope[ContextCollabStatusResponse],
)
@envelope_errors
async def get_context_collab_status(
    collab_context_id: str,
    request: Request,
    trunk_events: bool = False,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ContextCollabServiceProtocol = Injected(ContextCollabServiceProtocol),  # noqa: B008
) -> Envelope[ContextCollabStatusResponse]:
    result = service.get_status(
        collab_context_id, trunk_events=trunk_events, context=_actor_context(user)
    )
    return envelope(status_to_dto(result), request)
