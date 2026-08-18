"""OpenAPI v1 adapter for spaces, members and market favorites.

Application-only admission is intentionally refused in ``admission.py`` for
this first phase. The acting user therefore comes from the verified human
principal; no caller-supplied ``user_id`` is accepted by these routes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope, Page
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.principal import caller_owner_id
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope,
    envelope_errors,
    page,
)
from agentclaw.community.adapters.http.openapi_v1.spaces.schemas import (
    AddSpaceMemberRequest,
    CreateSpaceRequest,
    FavoriteAddedResult,
    FavoriteCanceledResult,
    FavoriteTargetRequest,
    MarketFavoriteItem,
    PersonalSpaceInitialized,
    SearchFavoritesRequest,
    SpaceCreated,
    SpaceItem,
    SpaceMemberDeletedResult,
    SpaceMemberItem,
    SpaceMemberMutationResult,
    UpdateSpaceMemberRoleRequest,
)
from agentclaw.community.api.market_favorite_service import (
    MarketFavoriteServiceProtocol,
)
from agentclaw.community.api.space_service import (
    SpaceMemberServiceProtocol,
    SpaceServiceProtocol,
)
from agentclaw.community.core.market_favorites.models import MarketFavoriteRecord
from agentclaw.community.core.spaces.models import (
    SpaceMemberSummaryRecord,
    SpaceRole,
    SpaceSummaryRecord,
    SpaceType,
)
from agentclaw.community.di import Injected


router = APIRouter(prefix="/openapi/v1/spaces", tags=["spaces"])
PrincipalDep = Annotated[Principal, Depends(require_principal)]
SpaceIdPath = Annotated[int, Path(ge=1, description="Space primary identifier.")]
PageNoQuery = Annotated[int, Query(ge=1)]
PageSizeQuery = Annotated[int, Query(ge=1, le=100)]


def _space_item(record: SpaceSummaryRecord) -> SpaceItem:
    return SpaceItem(
        space_id=record.space.id,
        space_code=record.space.space_code,
        space_name=record.space.name,
        space_type=record.space.space_type,
        current_user_role=record.current_user_role,
        join_status=record.join_status,
        member_count=record.member_count,
        owner_count=record.owner_count,
        gmt_modified=record.space.gmt_modified,
    )


def _created_space(record) -> SpaceCreated:
    return SpaceCreated(
        space_id=record.id,
        space_code=record.space_code,
        space_name=record.name,
        space_type=record.space_type,
        current_user_role=SpaceRole.OWNER,
        is_creator=True,
        member_count=1,
        owner_count=1,
        gmt_created=record.gmt_created,
        gmt_modified=record.gmt_modified,
    )


def _member_item(record: SpaceMemberSummaryRecord) -> SpaceMemberItem:
    return SpaceMemberItem(
        user_id=record.member.user_id,
        role=record.member.role,
        is_creator=record.is_creator,
        gmt_modified=record.member.gmt_modified,
    )


def _favorite_item(record: MarketFavoriteRecord) -> MarketFavoriteItem:
    return MarketFavoriteItem(
        favorite_id=record.id,
        target_type=record.target_type,
        target_code=record.target_code,
        favorite_at=record.gmt_created,
    )


@router.get("", response_model=Envelope[Page[SpaceItem]])
@envelope_errors
async def list_spaces(
    request: Request,
    principal: PrincipalDep,
    keyword: Annotated[str | None, Query(max_length=128)] = None,
    space_type: Annotated[SpaceType | None, Query()] = None,
    page_no: PageNoQuery = 1,
    page_size: PageSizeQuery = 20,
    service: SpaceServiceProtocol = Injected(SpaceServiceProtocol),
) -> Envelope[Page[SpaceItem]]:
    actor_id = caller_owner_id(principal)
    total, records = service.list_spaces(
        user_id=actor_id,
        keyword=keyword,
        space_type=space_type,
        page_no=page_no,
        page_size=page_size,
    )
    return page(total, [_space_item(record) for record in records], request)


@router.post(
    "/personal/initialize",
    response_model=Envelope[PersonalSpaceInitialized],
)
@envelope_errors
async def initialize_personal_space(
    request: Request,
    principal: PrincipalDep,
    service: SpaceServiceProtocol = Injected(SpaceServiceProtocol),
) -> Envelope[PersonalSpaceInitialized]:
    record, was_created = service.initialize_personal(
        user_id=caller_owner_id(principal)
    )
    result = PersonalSpaceInitialized(
        **_created_space(record).model_dump(), created=was_created
    )
    return envelope(result, request)


@router.post(
    "/create",
    status_code=201,
    response_model=Envelope[SpaceCreated],
)
@envelope_errors
async def create_team_space(
    body: CreateSpaceRequest,
    request: Request,
    principal: PrincipalDep,
    service: SpaceServiceProtocol = Injected(SpaceServiceProtocol),
) -> Envelope[SpaceCreated]:
    record = service.create_team(
        name=body.space_name, creator_id=caller_owner_id(principal)
    )
    return created(_created_space(record), request)


@router.get(
    "/{space_id}/members",
    response_model=Envelope[Page[SpaceMemberItem]],
)
@envelope_errors
async def list_space_members(
    space_id: SpaceIdPath,
    request: Request,
    principal: PrincipalDep,
    keyword: Annotated[str | None, Query(max_length=256)] = None,
    page_no: PageNoQuery = 1,
    page_size: PageSizeQuery = 20,
    service: SpaceMemberServiceProtocol = Injected(SpaceMemberServiceProtocol),
) -> Envelope[Page[SpaceMemberItem]]:
    total, records = service.list_members(
        space_id=space_id,
        actor_id=caller_owner_id(principal),
        keyword=keyword,
        page_no=page_no,
        page_size=page_size,
    )
    return page(total, [_member_item(record) for record in records], request)


@router.post(
    "/{space_id}/members",
    status_code=201,
    response_model=Envelope[SpaceMemberMutationResult],
)
@envelope_errors
async def add_space_member(
    space_id: SpaceIdPath,
    body: AddSpaceMemberRequest,
    request: Request,
    principal: PrincipalDep,
    service: SpaceMemberServiceProtocol = Injected(SpaceMemberServiceProtocol),
) -> Envelope[SpaceMemberMutationResult]:
    record = service.add_member(
        space_id=space_id,
        actor_id=caller_owner_id(principal),
        user_id=body.user_id,
        role=body.role,
    )
    return created(
        SpaceMemberMutationResult(
            space_id=space_id, user_id=record.user_id, role=record.role
        ),
        request,
    )


@router.delete(
    "/{space_id}/members/{user_id}",
    response_model=Envelope[SpaceMemberDeletedResult],
)
@envelope_errors
async def delete_space_member(
    space_id: SpaceIdPath,
    user_id: Annotated[str, Path(min_length=1, max_length=256)],
    request: Request,
    principal: PrincipalDep,
    service: SpaceMemberServiceProtocol = Injected(SpaceMemberServiceProtocol),
) -> Envelope[SpaceMemberDeletedResult]:
    service.delete_member(
        space_id=space_id,
        actor_id=caller_owner_id(principal),
        user_id=user_id,
    )
    return envelope(
        SpaceMemberDeletedResult(space_id=space_id, user_id=user_id), request
    )


@router.put(
    "/{space_id}/members/{user_id}/role",
    response_model=Envelope[SpaceMemberMutationResult],
)
@envelope_errors
async def update_space_member_role(
    space_id: SpaceIdPath,
    user_id: Annotated[str, Path(min_length=1, max_length=256)],
    body: UpdateSpaceMemberRoleRequest,
    request: Request,
    principal: PrincipalDep,
    service: SpaceMemberServiceProtocol = Injected(SpaceMemberServiceProtocol),
) -> Envelope[SpaceMemberMutationResult]:
    summary = service.update_role(
        space_id=space_id,
        actor_id=caller_owner_id(principal),
        user_id=user_id,
        role=body.role,
    )
    return envelope(
        SpaceMemberMutationResult(
            space_id=space_id,
            user_id=summary.member.user_id,
            role=summary.member.role,
        ),
        request,
    )


@router.post(
    "/{space_id}/market-favorites",
    response_model=Envelope[FavoriteAddedResult],
)
@envelope_errors
async def add_market_favorite(
    space_id: SpaceIdPath,
    body: FavoriteTargetRequest,
    request: Request,
    principal: PrincipalDep,
    service: MarketFavoriteServiceProtocol = Injected(MarketFavoriteServiceProtocol),
) -> Envelope[FavoriteAddedResult]:
    record = service.add(
        space_id=space_id,
        actor_id=caller_owner_id(principal),
        target_type=body.target_type,
        target_code=body.target_code,
    )
    return envelope(
        FavoriteAddedResult(
            favorite_id=record.id,
            target_type=record.target_type,
            target_code=record.target_code,
        ),
        request,
    )


@router.post(
    "/{space_id}/market-favorites/cancel",
    response_model=Envelope[FavoriteCanceledResult],
)
@envelope_errors
async def cancel_market_favorite(
    space_id: SpaceIdPath,
    body: FavoriteTargetRequest,
    request: Request,
    principal: PrincipalDep,
    service: MarketFavoriteServiceProtocol = Injected(MarketFavoriteServiceProtocol),
) -> Envelope[FavoriteCanceledResult]:
    service.cancel(
        space_id=space_id,
        actor_id=caller_owner_id(principal),
        target_type=body.target_type,
        target_code=body.target_code,
    )
    return envelope(
        FavoriteCanceledResult(
            target_type=body.target_type,
            target_code=body.target_code.strip(),
        ),
        request,
    )


@router.post(
    "/{space_id}/market-favorites/search",
    response_model=Envelope[Page[MarketFavoriteItem]],
)
@envelope_errors
async def search_market_favorites(
    space_id: SpaceIdPath,
    body: SearchFavoritesRequest,
    request: Request,
    principal: PrincipalDep,
    service: MarketFavoriteServiceProtocol = Injected(MarketFavoriteServiceProtocol),
) -> Envelope[Page[MarketFavoriteItem]]:
    total, records = service.search(
        space_id=space_id,
        actor_id=caller_owner_id(principal),
        target_type=body.target_type,
        keyword=body.keyword,
        page_no=body.page_no,
        page_size=body.page_size,
    )
    return page(total, [_favorite_item(record) for record in records], request)
