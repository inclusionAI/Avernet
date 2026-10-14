"""Script-friendly Space maintenance restricted to authenticated super-admins."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.spaces_management.dependencies import (
    require_space_super_admin,
)
from agentclaw.community.adapters.http.spaces_management.responses import (
    ManagementResponse,
    ManagementRoute,
    execute,
)
from agentclaw.community.api.space_management_service import (
    SpaceManagementServiceProtocol,
)
from agentclaw.community.core.spaces.management_models import (
    CreateTeam,
    InitializeTeam,
    ManagedSpace,
    MemberBatch,
    MemberPageQuery,
    RenameTeam,
    SetMemberRoles,
    SpacePageQuery,
)
from agentclaw.community.di import Injected

router = APIRouter(
    prefix="/api/spaces",
    tags=["space-maintenance"],
    route_class=ManagementRoute,
    dependencies=[Depends(require_space_super_admin)],
)
SpaceId = Annotated[int, Path(ge=1)]
Service = Annotated[
    SpaceManagementServiceProtocol, Injected(SpaceManagementServiceProtocol)
]
Caller = Annotated[AuthenticatedUser, Depends(require_space_super_admin)]


@router.post("", status_code=201, response_model=ManagementResponse[ManagedSpace])
def create_team(body: CreateTeam, request: Request, service: Service):
    return execute(request, service.create_team, InitializeTeam(**body.model_dump()))


@router.post(
    "/initialize", status_code=201, response_model=ManagementResponse[ManagedSpace]
)
def initialize_team(body: InitializeTeam, request: Request, service: Service):
    return execute(request, service.create_team, body)


@router.get("", response_model=ManagementResponse[dict])
def list_teams(
    request: Request, service: Service, query: Annotated[SpacePageQuery, Query()]
):
    return execute(request, service.list_teams, query)


@router.get("/{space_id}", response_model=ManagementResponse[ManagedSpace])
def get_team(space_id: SpaceId, request: Request, service: Service):
    return execute(request, service.get_team, space_id)


@router.post("/{space_id}/update", response_model=ManagementResponse[dict])
def rename_team(
    space_id: SpaceId,
    body: RenameTeam,
    request: Request,
    service: Service,
    caller: Caller,
):
    return execute(request, service.rename_team, space_id, body, caller.staffId)


@router.delete("/{space_id}", response_model=ManagementResponse[dict])
def delete_empty_team(space_id: SpaceId, request: Request, service: Service):
    return execute(request, service.delete_empty_team, space_id)


@router.post("/{space_id}/delete-with-members", response_model=ManagementResponse[dict])
def delete_team_with_members(space_id: SpaceId, request: Request, service: Service):
    return execute(request, service.delete_team_with_members, space_id)


@router.get("/{space_id}/members", response_model=ManagementResponse[dict])
def list_members(
    space_id: SpaceId,
    request: Request,
    service: Service,
    query: Annotated[MemberPageQuery, Query()],
):
    return execute(request, service.list_members, space_id, query)


@router.post("/{space_id}/members/batch-add", response_model=ManagementResponse[dict])
def add_members(
    space_id: SpaceId,
    body: MemberBatch,
    request: Request,
    service: Service,
    caller: Caller,
):
    return execute(request, service.add_members, space_id, body, caller.staffId)


@router.post(
    "/{space_id}/members/batch-remove", response_model=ManagementResponse[dict]
)
def remove_members(
    space_id: SpaceId, body: MemberBatch, request: Request, service: Service
):
    return execute(request, service.remove_members, space_id, body)


@router.put("/{space_id}/members/roles", response_model=ManagementResponse[dict])
def set_member_roles(
    space_id: SpaceId, body: SetMemberRoles, request: Request, service: Service
):
    return execute(request, service.set_member_roles, space_id, body)
