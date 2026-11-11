"""Task context-collab 公开 HTTP adapter —— /openapi/v1 双面规则(spec §6.1)。

``adapters/http/task/context_collab.py`` 的公开孪生:同一条
``ContextCollabServiceProtocol``,DTO/命令组装/Envelope/错误映射全部复用
(``schemas_context_collab.py`` 单处维护),本文件只做公开面特有的一件事
——身份转换:gateway 签发的 ``X-Avernet-Principal`` 经
``require_principal`` 验证后,取其 end-user id 构造 L2 责任主体
(与 openapi_v1/task/router.py 现有 ``principal.user_id`` 派生同一写法),
``ActorContext.actor`` 由此绑定,``EventCreateRequest.actor_ref`` 对账照旧。

路径(spec §6.1/§14.1,singular ``task``):
POST /openapi/v1/collaboration/task/context_collab                          — 创建协作上下文
POST /openapi/v1/collaboration/task/context_collab/{collab_context_id}/events — 追加事件
GET  /openapi/v1/collaboration/task/context_collab/{collab_context_id}       — 查询状态/任务进度

统一返回协议同 task 公开面:成功经 ``envelope()``,领域错误由
``@envelope_errors`` + ``ENVELOPE_ERRORS`` 的 ContextCollab 行映射
ErrorEnvelope(422/409/404),router 不手写 ``HTTPException``。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    refuse_app_only_caller,
)
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

router = APIRouter(
    prefix="/openapi/v1/collaboration/task/context_collab",
    tags=["task-context-collab"],
    route_class=PublicAPIRoute,
    # Every operation here is REFUSED in ``admission.py`` — the operator is the
    # L2 accountability subject the delivery binds to the verified end user —
    # so the whole group declares the refusal the same way classification
    # does. The central check stays authoritative; this makes the decision
    # visible on the route and safe against a mislabelled table entry.
    dependencies=[Depends(refuse_app_only_caller)],
)

# Handler-level principal dependency: the verified caller is the identity this
# face binds the L2 actor to, so unlike the task group's execute/dashboard it
# is *used*, never deleted.
PrincipalDep = Annotated[Principal, Depends(require_principal)]

CollabContextIdPath = Annotated[
    str,
    Path(
        min_length=1,
        description="Collaboration context id exactly as returned by the "
        "create operation (collab_context_id in the response data).",
    ),
]


def _actor_context(principal: Principal) -> ActorContext:
    """已验证 principal → 服务调用上下文;操作者是 L2 责任主体,取 gateway 认证的 end-user id。"""
    return ActorContext(
        actor=PrincipalRef("human", principal.user_id), source="openapi"
    )


@router.post("", response_model=Envelope[ContextCollabCreatedResultDto])
@envelope_errors
async def create_context_collab(
    body: ColletCreateRequest,
    request: Request,
    principal: PrincipalDep,
    service: ContextCollabServiceProtocol = Injected(ContextCollabServiceProtocol),  # noqa: B008
) -> Envelope[ContextCollabCreatedResultDto]:
    result = service.create_context(body.to_domain(), _actor_context(principal))
    return envelope(created_result_to_dto(result), request)


@router.post(
    "/{collab_context_id}/events",
    response_model=Envelope[EventAcceptedResultDto],
)
@envelope_errors
async def append_context_collab_event(
    collab_context_id: CollabContextIdPath,
    body: EventCreateRequest,
    request: Request,
    principal: PrincipalDep,
    service: ContextCollabServiceProtocol = Injected(ContextCollabServiceProtocol),  # noqa: B008
) -> Envelope[EventAcceptedResultDto]:
    context = _actor_context(principal)
    command = body.to_domain(context.actor)
    result = service.append_event(collab_context_id, command, context)
    return envelope(event_accepted_to_dto(result), request)


@router.post(
    "/{collab_context_id}/run",
    response_model=Envelope[ContextCollabRunResultDto],
)
@envelope_errors
async def run_context_collab(
    collab_context_id: CollabContextIdPath,
    request: Request,
    principal: PrincipalDep,
    service: ContextCollabServiceProtocol = Injected(ContextCollabServiceProtocol),  # noqa: B008
) -> Envelope[ContextCollabRunResultDto]:
    result = await service.run_context(collab_context_id, _actor_context(principal))
    return envelope(run_result_to_dto(result), request)


@router.get(
    "/{collab_context_id}",
    response_model=Envelope[ContextCollabStatusResponse],
)
@envelope_errors
async def get_context_collab_status(
    collab_context_id: CollabContextIdPath,
    request: Request,
    principal: PrincipalDep,
    trunk_events: bool = Query(
        default=False,
        description="Return only the trunk (the parent-linked chain up to the "
        "current event) instead of the full task progress event list.",
    ),
    service: ContextCollabServiceProtocol = Injected(ContextCollabServiceProtocol),  # noqa: B008
) -> Envelope[ContextCollabStatusResponse]:
    result = service.get_status(
        collab_context_id,
        trunk_events=trunk_events,
        context=_actor_context(principal),
    )
    return envelope(status_to_dto(result), request)
