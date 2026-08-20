"""Thin HTTP adapter for canonical Bot-scoped SkillSet operations."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Path, Request, Response

from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Deleted, Envelope
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import deleted, envelope, envelope_errors
from agentclaw.community.api.skill_set_control_plane import SkillSetControlPlaneServiceProtocol
from agentclaw.community.di import Injected

from .schemas import (
    CreateSkillSetRequest, SkillSetItem, SkillSetMembershipResult,
    RequestMcpPermissions, SkillSetMcpItem, SkillSetMcpPermission,
    SkillSetMcpPermissionRequest, SkillSetResourceItem, SkillSetSkillItem,
    UpdateSkillSetRequest,
)

router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/skill-sets", tags=["skill-sets"])
SetIdPath = Annotated[str, Path(description="Decimal SkillSet identifier.")]
SkillIdPath = Annotated[str, Path(description="Decimal Skill identifier.")]
McpServerCodePath = Annotated[str, Path(description="Opaque MCP server identifier.")]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, description="Client key for idempotent SkillSet creation.")]


def _set(item: dict[str, Any]) -> SkillSetItem:
    return SkillSetItem(
        id=str(item["id"]), name=str(item["name"]), description=item.get("description"),
        is_default=bool(item.get("is_default")), is_active=bool(item.get("is_active")),
    )


@router.get("", response_model=Envelope[list[SkillSetItem]])
@envelope_errors
async def list_skill_sets(bot_id: BotIdPath, actor_id: UserIdDep, request: Request, service: SkillSetControlPlaneServiceProtocol = Injected(SkillSetControlPlaneServiceProtocol)) -> Envelope[list[SkillSetItem]]:
    return envelope([_set(item) for item in service.list_sets(bot_id=bot_id, actor_id=actor_id)], request)


@router.post("", response_model=Envelope[SkillSetItem], status_code=201)
@envelope_errors
async def create_skill_set(bot_id: BotIdPath, payload: CreateSkillSetRequest, idempotency_key: IdempotencyKey, actor_id: UserIdDep, request: Request, response: Response, service: SkillSetControlPlaneServiceProtocol = Injected(SkillSetControlPlaneServiceProtocol)) -> Envelope[SkillSetItem]:
    item = service.create_set(bot_id=bot_id, actor_id=actor_id, name=payload.name, description=payload.description, idempotency_key=idempotency_key)
    response.status_code = 201
    return envelope(_set(item), request, code=201000)


@router.get("/resources", response_model=Envelope[list[SkillSetResourceItem]])
@envelope_errors
async def resources(bot_id: BotIdPath, actor_id: UserIdDep, request: Request, service: SkillSetControlPlaneServiceProtocol = Injected(SkillSetControlPlaneServiceProtocol)) -> Envelope[list[SkillSetResourceItem]]:
    return envelope([SkillSetResourceItem(**_set(item).model_dump(), mcps=item.get("mcps", []), clis=item.get("clis", [])) for item in service.resources(bot_id=bot_id, actor_id=actor_id)], request)


@router.get("/{set_id}", response_model=Envelope[SkillSetItem])
@envelope_errors
async def get_skill_set(bot_id: BotIdPath, set_id: SetIdPath, actor_id: UserIdDep, request: Request, service: SkillSetControlPlaneServiceProtocol = Injected(SkillSetControlPlaneServiceProtocol)) -> Envelope[SkillSetItem]:
    return envelope(_set(service.get_set(bot_id=bot_id, actor_id=actor_id, set_id=set_id)), request)


@router.put("/{set_id}", response_model=Envelope[SkillSetItem])
@envelope_errors
async def update_skill_set(bot_id: BotIdPath, set_id: SetIdPath, payload: UpdateSkillSetRequest, actor_id: UserIdDep, request: Request, service: SkillSetControlPlaneServiceProtocol = Injected(SkillSetControlPlaneServiceProtocol)) -> Envelope[SkillSetItem]:
    return envelope(_set(service.update_set(bot_id=bot_id, actor_id=actor_id, set_id=set_id, name=payload.name, description=payload.description)), request)


@router.delete("/{set_id}", response_model=Envelope[Deleted])
@envelope_errors
async def delete_skill_set(bot_id: BotIdPath, set_id: SetIdPath, actor_id: UserIdDep, request: Request, service: SkillSetControlPlaneServiceProtocol = Injected(SkillSetControlPlaneServiceProtocol)) -> Envelope[Deleted]:
    service.delete_set(bot_id=bot_id, actor_id=actor_id, set_id=set_id)
    return deleted(request)


@router.get("/{set_id}/skills", response_model=Envelope[list[SkillSetSkillItem]])
@envelope_errors
async def list_set_skills(bot_id: BotIdPath, set_id: SetIdPath, actor_id: UserIdDep, request: Request, service: SkillSetControlPlaneServiceProtocol = Injected(SkillSetControlPlaneServiceProtocol)) -> Envelope[list[SkillSetSkillItem]]:
    return envelope([SkillSetSkillItem(skill_id=str(item["id"]), name=str(item["name"]), description=item.get("description")) for item in service.list_skills(bot_id=bot_id, actor_id=actor_id, set_id=set_id)], request)


@router.put("/{set_id}/skills/{skill_id}", response_model=Envelope[SkillSetMembershipResult])
@envelope_errors
async def add_skill(bot_id: BotIdPath, set_id: SetIdPath, skill_id: SkillIdPath, actor_id: UserIdDep, request: Request, service: SkillSetControlPlaneServiceProtocol = Injected(SkillSetControlPlaneServiceProtocol)) -> Envelope[SkillSetMembershipResult]:
    return envelope(SkillSetMembershipResult(**await service.add_skill(bot_id=bot_id, actor_id=actor_id, set_id=set_id, skill_id=skill_id)), request)


@router.delete("/{set_id}/skills/{skill_id}", response_model=Envelope[SkillSetMembershipResult])
@envelope_errors
async def remove_skill(bot_id: BotIdPath, set_id: SetIdPath, skill_id: SkillIdPath, actor_id: UserIdDep, request: Request, service: SkillSetControlPlaneServiceProtocol = Injected(SkillSetControlPlaneServiceProtocol)) -> Envelope[SkillSetMembershipResult]:
    return envelope(SkillSetMembershipResult(**await service.remove_skill(bot_id=bot_id, actor_id=actor_id, set_id=set_id, skill_id=skill_id)), request)


@router.get("/{set_id}/mcps", response_model=Envelope[list[SkillSetMcpItem]])
@envelope_errors
async def list_set_mcps(bot_id: BotIdPath, set_id: SetIdPath, actor_id: UserIdDep, request: Request, service: SkillSetControlPlaneServiceProtocol = Injected(SkillSetControlPlaneServiceProtocol)) -> Envelope[list[SkillSetMcpItem]]:
    return envelope([SkillSetMcpItem(**item) for item in service.list_mcps(bot_id=bot_id, actor_id=actor_id, set_id=set_id)], request)


@router.get("/{set_id}/mcp-permissions", response_model=Envelope[list[SkillSetMcpPermission]])
@envelope_errors
async def mcp_permissions(bot_id: BotIdPath, set_id: SetIdPath, actor_id: UserIdDep, request: Request, service: SkillSetControlPlaneServiceProtocol = Injected(SkillSetControlPlaneServiceProtocol)) -> Envelope[list[SkillSetMcpPermission]]:
    return envelope([SkillSetMcpPermission(**item) for item in service.mcp_permissions(bot_id=bot_id, actor_id=actor_id, set_id=set_id)], request)


@router.post("/{set_id}/mcp-permission-requests", response_model=Envelope[list[SkillSetMcpPermissionRequest]])
@envelope_errors
async def request_mcp_permissions(bot_id: BotIdPath, set_id: SetIdPath, payload: RequestMcpPermissions, actor_id: UserIdDep, request: Request, service: SkillSetControlPlaneServiceProtocol = Injected(SkillSetControlPlaneServiceProtocol)) -> Envelope[list[SkillSetMcpPermissionRequest]]:
    return envelope([SkillSetMcpPermissionRequest(**item) for item in service.request_mcp_permissions(bot_id=bot_id, actor_id=actor_id, set_id=set_id, reason=payload.reason)], request)


@router.put("/{set_id}/mcps/{server_code}", response_model=Envelope[SkillSetMembershipResult])
@envelope_errors
async def add_mcp(bot_id: BotIdPath, set_id: SetIdPath, server_code: McpServerCodePath, actor_id: UserIdDep, request: Request, service: SkillSetControlPlaneServiceProtocol = Injected(SkillSetControlPlaneServiceProtocol)) -> Envelope[SkillSetMembershipResult]:
    return envelope(SkillSetMembershipResult(**await service.add_mcp(bot_id=bot_id, actor_id=actor_id, set_id=set_id, server_code=server_code)), request)


@router.delete("/{set_id}/mcps/{server_code}", response_model=Envelope[SkillSetMembershipResult])
@envelope_errors
async def remove_mcp(bot_id: BotIdPath, set_id: SetIdPath, server_code: McpServerCodePath, actor_id: UserIdDep, request: Request, service: SkillSetControlPlaneServiceProtocol = Injected(SkillSetControlPlaneServiceProtocol)) -> Envelope[SkillSetMembershipResult]:
    return envelope(SkillSetMembershipResult(**await service.remove_mcp(bot_id=bot_id, actor_id=actor_id, set_id=set_id, server_code=server_code)), request)


@router.post("/{set_id}/activate", response_model=Envelope[SkillSetItem])
@envelope_errors
async def activate(bot_id: BotIdPath, set_id: SetIdPath, actor_id: UserIdDep, request: Request, service: SkillSetControlPlaneServiceProtocol = Injected(SkillSetControlPlaneServiceProtocol)) -> Envelope[SkillSetItem]:
    return envelope(_set(await service.activate(bot_id=bot_id, actor_id=actor_id, set_id=set_id)), request)


@router.post("/{set_id}/deactivate", response_model=Envelope[SkillSetItem])
@envelope_errors
async def deactivate(bot_id: BotIdPath, set_id: SetIdPath, actor_id: UserIdDep, request: Request, service: SkillSetControlPlaneServiceProtocol = Injected(SkillSetControlPlaneServiceProtocol)) -> Envelope[SkillSetItem]:
    return envelope(_set(await service.deactivate(bot_id=bot_id, actor_id=actor_id, set_id=set_id)), request)
