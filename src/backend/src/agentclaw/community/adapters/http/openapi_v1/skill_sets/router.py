"""Thin HTTP adapter for canonical Bot-scoped SkillSet operations."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request, Response

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Deleted,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    require_granted_addressed_bot,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    deleted,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.skill_set_management_service import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.di import Injected

from .schemas import (
    CreateSkillSetRequest,
    RequestMcpPermissions,
    SkillSetItem,
    SkillSetMcpItem,
    SkillSetMcpPermission,
    SkillSetMcpPermissionRequest,
    SkillSetMembershipResult,
    SkillSetResourceItem,
    SkillSetSkillItem,
    UpdateSkillSetRequest,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

_GRANT_CHECKED_ADDRESSED_BOT = [Depends(require_granted_addressed_bot)]

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/skill-sets",
    tags=["skill-sets"],
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
    route_class=PublicAPIRoute,
)
SetIdPath = Annotated[str, Path(description="Decimal SkillSet identifier.")]
SkillIdPath = Annotated[str, Path(description="Decimal Skill identifier.")]
McpServerCodePath = Annotated[
    str,
    Path(description="Opaque MCP server identifier."),
]


def _set(item: dict[str, Any]) -> SkillSetItem:
    return SkillSetItem(
        id=str(item["id"]),
        name=str(item["name"]),
        description=item.get("description"),
        is_default=bool(item.get("is_default")),
        is_active=bool(item.get("is_active")),
    )


@router.get("", response_model=Envelope[list[SkillSetItem]])
@envelope_errors
async def list_skill_sets(
    bot_id: BotIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> Envelope[list[SkillSetItem]]:
    items = service.list_sets(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
    )
    return envelope([_set(item) for item in items], request)


@router.post("", response_model=Envelope[SkillSetItem], status_code=201)
@envelope_errors
async def create_skill_set(
    bot_id: BotIdPath,
    payload: CreateSkillSetRequest,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    response: Response,
    service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> Envelope[SkillSetItem]:
    item = service.create_set(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        name=payload.name,
        description=payload.description,
    )
    response.status_code = 201
    return envelope(_set(item), request, code=201000)


@router.get("/resources", response_model=Envelope[list[SkillSetResourceItem]])
@envelope_errors
async def resources(
    bot_id: BotIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> Envelope[list[SkillSetResourceItem]]:
    items = service.list_resources(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
    )
    return envelope(
        [
            SkillSetResourceItem(
                **_set(item).model_dump(),
                mcps=item.get("mcps", []),
                clis=item.get("clis", []),
            )
            for item in items
        ],
        request,
    )


@router.get("/{set_id}", response_model=Envelope[SkillSetItem])
@envelope_errors
async def get_skill_set(
    bot_id: BotIdPath,
    set_id: SetIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> Envelope[SkillSetItem]:
    item = service.get_set(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        set_id=set_id,
    )
    return envelope(_set(item), request)


@router.put("/{set_id}", response_model=Envelope[SkillSetItem])
@envelope_errors
async def update_skill_set(
    bot_id: BotIdPath,
    set_id: SetIdPath,
    payload: UpdateSkillSetRequest,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> Envelope[SkillSetItem]:
    item = service.update_set(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        set_id=set_id,
        name=payload.name,
        description=payload.description,
    )
    return envelope(_set(item), request)


@router.delete("/{set_id}", response_model=Envelope[Deleted])
@envelope_errors
async def delete_skill_set(
    bot_id: BotIdPath,
    set_id: SetIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> Envelope[Deleted]:
    service.delete_set(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        set_id=set_id,
    )
    return deleted(request)


@router.get("/{set_id}/skills", response_model=Envelope[list[SkillSetSkillItem]])
@envelope_errors
async def list_set_skills(
    bot_id: BotIdPath,
    set_id: SetIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> Envelope[list[SkillSetSkillItem]]:
    items = service.list_skills(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        set_id=set_id,
    )
    return envelope(
        [
            SkillSetSkillItem(
                skill_id=str(item["id"]),
                name=str(item["name"]),
                description=item.get("description"),
            )
            for item in items
        ],
        request,
    )


@router.put(
    "/{set_id}/skills/{skill_id}",
    response_model=Envelope[SkillSetMembershipResult],
)
@envelope_errors
async def add_skill(
    bot_id: BotIdPath,
    set_id: SetIdPath,
    skill_id: SkillIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> Envelope[SkillSetMembershipResult]:
    (result,) = await service.add_skills(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        set_id=set_id,
        skill_ids=[skill_id],
    )
    if result.error is not None:
        raise result.error
    return envelope(SkillSetMembershipResult(changed=result.changed), request)


@router.delete(
    "/{set_id}/skills/{skill_id}",
    response_model=Envelope[SkillSetMembershipResult],
)
@envelope_errors
async def remove_skill(
    bot_id: BotIdPath,
    set_id: SetIdPath,
    skill_id: SkillIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> Envelope[SkillSetMembershipResult]:
    (result,) = await service.remove_skills(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        set_id=set_id,
        skill_ids=[skill_id],
    )
    if result.error is not None:
        raise result.error
    return envelope(SkillSetMembershipResult(changed=result.changed), request)


@router.get("/{set_id}/mcps", response_model=Envelope[list[SkillSetMcpItem]])
@envelope_errors
async def list_set_mcps(
    bot_id: BotIdPath,
    set_id: SetIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> Envelope[list[SkillSetMcpItem]]:
    items = service.list_mcps(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        set_id=set_id,
    )
    return envelope([SkillSetMcpItem(**item) for item in items], request)


@router.get(
    "/{set_id}/mcp-permissions",
    response_model=Envelope[list[SkillSetMcpPermission]],
)
@envelope_errors
async def mcp_permissions(
    bot_id: BotIdPath,
    set_id: SetIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> Envelope[list[SkillSetMcpPermission]]:
    items = service.list_mcp_permissions(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        set_id=set_id,
    )
    return envelope([SkillSetMcpPermission(**item) for item in items], request)


@router.post(
    "/{set_id}/mcp-permission-requests",
    response_model=Envelope[list[SkillSetMcpPermissionRequest]],
)
@envelope_errors
async def request_mcp_permissions(
    bot_id: BotIdPath,
    set_id: SetIdPath,
    payload: RequestMcpPermissions,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> Envelope[list[SkillSetMcpPermissionRequest]]:
    items = service.request_mcp_permissions(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        set_id=set_id,
        reason=payload.reason,
    )
    return envelope(
        [SkillSetMcpPermissionRequest(**item) for item in items],
        request,
    )


@router.put(
    "/{set_id}/mcps/{server_code}",
    response_model=Envelope[SkillSetMembershipResult],
)
@envelope_errors
async def add_mcp(
    bot_id: BotIdPath,
    set_id: SetIdPath,
    server_code: McpServerCodePath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> Envelope[SkillSetMembershipResult]:
    result = await service.add_mcp(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        set_id=set_id,
        server_code=server_code,
    )
    return envelope(SkillSetMembershipResult(**result), request)


@router.delete(
    "/{set_id}/mcps/{server_code}",
    response_model=Envelope[SkillSetMembershipResult],
)
@envelope_errors
async def remove_mcp(
    bot_id: BotIdPath,
    set_id: SetIdPath,
    server_code: McpServerCodePath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> Envelope[SkillSetMembershipResult]:
    result = await service.remove_mcp(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        set_id=set_id,
        server_code=server_code,
    )
    return envelope(SkillSetMembershipResult(**result), request)


@router.post("/{set_id}/activate", response_model=Envelope[SkillSetItem])
@envelope_errors
async def activate(
    bot_id: BotIdPath,
    set_id: SetIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> Envelope[SkillSetItem]:
    item = await service.activate(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        set_id=set_id,
    )
    return envelope(_set(item), request)


@router.post("/{set_id}/deactivate", response_model=Envelope[SkillSetItem])
@envelope_errors
async def deactivate(
    bot_id: BotIdPath,
    set_id: SetIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    service: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> Envelope[SkillSetItem]:
    item = await service.deactivate(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        set_id=set_id,
    )
    return envelope(_set(item), request)
