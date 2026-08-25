"""OpenAPI v1 adapter for spaces, members and market favorites.

Every user-scoped operation names its acting user through the shared
``user_id`` query dependency. Admission decides whether an application may
reach the operation; Space ownership rules remain in the core services.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope, Page
from agentclaw.community.adapters.http.openapi_v1.errors import GrantNotResolvableError
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    UserIdDep,
    refuse_app_only_caller,
)
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
    FavoriteStatusesRequest,
    FavoriteStatusesResult,
    FavoriteTargetRequest,
    MarketFavoriteItem,
    PersonalSpaceInitialized,
    SearchFavoritesRequest,
    SpaceCreated,
    SpaceRole,
    SpaceType,
    SpaceItem,
    SpaceMemberDeletedResult,
    SpaceMemberItem,
    SpaceMemberMutationResult,
    SpaceSkillItem,
    UpdateSpaceMemberRoleRequest,
)
from agentclaw.community.api.market_favorite_service import (
    MarketFavoriteServiceProtocol,
)
from agentclaw.community.api.space_service import (
    SpaceMemberServiceProtocol,
    SpaceServiceProtocol,
)
from agentclaw.community.api.space_skill_query_service import (
    SpaceSkillQueryServiceProtocol,
)
from agentclaw.community.core.market_favorites.models import (
    FavoriteTargetType as DomainFavoriteTargetType,
    MarketSource as DomainMarketSource,
    MarketFavoriteRecord,
)
from agentclaw.community.core.spaces.models import (
    SpaceMemberSummaryRecord,
    SpaceRole as DomainSpaceRole,
    SpaceSummaryRecord,
    SpaceType as DomainSpaceType,
)
from agentclaw.community.core.repository.protocols.skill_center_types import (
    SpaceSkillSummaryRecord,
)
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute


router = APIRouter(
    prefix="/openapi/v1/bots/spaces", tags=["spaces"], route_class=PublicAPIRoute
)
SpaceIdPath = Annotated[int, Path(ge=1, description="Space primary identifier.")]
PageNoQuery = Annotated[int, Query(ge=1, description="One-based page number.")]
PageSizeQuery = Annotated[
    int, Query(ge=1, le=100, description="Maximum items returned per page.")
]
_REFUSES_APP_ONLY = [Depends(refuse_app_only_caller)]


def _require_user_delegation(caller: ActingCaller) -> str:
    granted = caller.granted_bot_ids()
    if granted is not None and not granted:
        raise GrantNotResolvableError(
            "application holds no live delegation from the named user"
        )
    return caller.user_id


def _space_item(record: SpaceSummaryRecord) -> SpaceItem:
    return SpaceItem(
        space_id=record.space.id,
        space_code=record.space.space_code,
        space_name=record.space.name,
        space_type=record.space.space_type,
        creator_user_id=record.space.created_by,
        creator_user_name=record.creator_user_name,
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
        current_user_role=SpaceRole.ADMIN,
        is_creator=True,
        member_count=1,
        owner_count=1,
        gmt_created=record.gmt_created,
        gmt_modified=record.gmt_modified,
    )


def _member_item(record: SpaceMemberSummaryRecord) -> SpaceMemberItem:
    return SpaceMemberItem(
        user_id=record.member.user_id,
        user_name=record.member.user_name,
        display_name=record.display_name,
        role=record.member.role,
        is_creator=record.is_creator,
        gmt_modified=record.member.gmt_modified,
    )


def _favorite_item(record: MarketFavoriteRecord) -> MarketFavoriteItem:
    return MarketFavoriteItem(
        favorite_id=record.id,
        market_source=record.market_source,
        target_type=record.target_type,
        target_code=record.target_code,
        favorite_at=record.gmt_created,
    )


def _space_skill_item(record: SpaceSkillSummaryRecord) -> SpaceSkillItem:
    return SpaceSkillItem(
        skill_id=str(record["id"]),
        skill_uuid=record["skill_uuid"],
        name=record["name"],
        description=record["description"],
        status=record["status"],
        draft_status=record["draft_status"],
        space_type=record["space_type"],
        current_user_skill_role=record["current_user_skill_role"],
        can_edit=record["can_edit"],
        can_grant=record["can_grant"],
        can_apply_edit=record["can_apply_edit"],
        gmt_created=record["gmt_created"],
        gmt_modified=record["gmt_modified"],
    )


@router.get("", response_model=Envelope[Page[SpaceItem]])
@envelope_errors
async def list_spaces(
    request: Request,
    caller: ActingCallerDep,
    keyword: Annotated[
        str | None,
        Query(max_length=128, description="Optional Space-name search text."),
    ] = None,
    space_type: Annotated[
        SpaceType | None, Query(description="Optional Space type filter.")
    ] = None,
    page_no: PageNoQuery = 1,
    page_size: PageSizeQuery = 20,
    service: SpaceServiceProtocol = Injected(SpaceServiceProtocol),
) -> Envelope[Page[SpaceItem]]:
    actor_id = _require_user_delegation(caller)
    total, records = service.list_spaces(
        user_id=actor_id,
        keyword=keyword,
        space_type=DomainSpaceType(space_type) if space_type is not None else None,
        page_no=page_no,
        page_size=page_size,
    )
    return page(total, [_space_item(record) for record in records], request)


@router.post(
    "/personal/initialize",
    response_model=Envelope[PersonalSpaceInitialized],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def initialize_personal_space(
    request: Request,
    user_id: UserIdDep,
    service: SpaceServiceProtocol = Injected(SpaceServiceProtocol),
) -> Envelope[PersonalSpaceInitialized]:
    record, was_created = service.initialize_personal(user_id=user_id)
    result = PersonalSpaceInitialized(
        **_created_space(record).model_dump(), created=was_created
    )
    return envelope(result, request)


@router.post(
    "/create",
    status_code=201,
    response_model=Envelope[SpaceCreated],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def create_team_space(
    body: CreateSpaceRequest,
    request: Request,
    user_id: UserIdDep,
    service: SpaceServiceProtocol = Injected(SpaceServiceProtocol),
) -> Envelope[SpaceCreated]:
    record = service.create_team(name=body.space_name, creator_id=user_id)
    return created(_created_space(record), request)


@router.get(
    "/{space_id}/members",
    response_model=Envelope[Page[SpaceMemberItem]],
)
@envelope_errors
async def list_space_members(
    space_id: SpaceIdPath,
    request: Request,
    caller: ActingCallerDep,
    keyword: Annotated[
        str | None, Query(max_length=256, description="Optional member-id search text.")
    ] = None,
    page_no: PageNoQuery = 1,
    page_size: PageSizeQuery = 20,
    service: SpaceMemberServiceProtocol = Injected(SpaceMemberServiceProtocol),
) -> Envelope[Page[SpaceMemberItem]]:
    actor_id = _require_user_delegation(caller)
    total, records = service.list_members(
        space_id=space_id,
        actor_id=actor_id,
        keyword=keyword,
        page_no=page_no,
        page_size=page_size,
    )
    return page(total, [_member_item(record) for record in records], request)


@router.get(
    "/{space_id}/skills",
    response_model=Envelope[Page[SpaceSkillItem]],
)
@envelope_errors
async def list_space_skills(
    request: Request,
    caller: ActingCallerDep,
    space_id: SpaceIdPath,
    keyword: Annotated[
        str | None,
        Query(
            max_length=128,
            description="Optional Skill-name or description search text.",
        ),
    ] = None,
    page_no: PageNoQuery = 1,
    page_size: PageSizeQuery = 20,
    service: SpaceSkillQueryServiceProtocol = Injected(SpaceSkillQueryServiceProtocol),
) -> Envelope[Page[SpaceSkillItem]]:
    actor_id = _require_user_delegation(caller)
    total, records = service.list_space_skills(
        space_id=space_id,
        actor_id=actor_id,
        keyword=keyword,
        page_no=page_no,
        page_size=page_size,
    )
    return page(total, [_space_skill_item(record) for record in records], request)


@router.post(
    "/{space_id}/members",
    status_code=201,
    response_model=Envelope[SpaceMemberMutationResult],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def add_space_member(
    space_id: SpaceIdPath,
    body: AddSpaceMemberRequest,
    request: Request,
    user_id: UserIdDep,
    service: SpaceMemberServiceProtocol = Injected(SpaceMemberServiceProtocol),
) -> Envelope[SpaceMemberMutationResult]:
    record = service.add_member(
        space_id=space_id,
        actor_id=user_id,
        user_id=body.member_user_id,
        role=DomainSpaceRole(body.role),
    )
    return created(
        SpaceMemberMutationResult(
            space_id=space_id, user_id=record.user_id, role=record.role
        ),
        request,
    )


@router.delete(
    "/{space_id}/members/{member_user_id}",
    response_model=Envelope[SpaceMemberDeletedResult],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def delete_space_member(
    space_id: SpaceIdPath,
    member_user_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=256,
            description="Identifier of the Space member to remove.",
        ),
    ],
    request: Request,
    user_id: UserIdDep,
    service: SpaceMemberServiceProtocol = Injected(SpaceMemberServiceProtocol),
) -> Envelope[SpaceMemberDeletedResult]:
    service.delete_member(
        space_id=space_id,
        actor_id=user_id,
        user_id=member_user_id,
    )
    return envelope(
        SpaceMemberDeletedResult(space_id=space_id, user_id=member_user_id), request
    )


@router.put(
    "/{space_id}/members/{member_user_id}/role",
    response_model=Envelope[SpaceMemberMutationResult],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def update_space_member_role(
    space_id: SpaceIdPath,
    member_user_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=256,
            description="Identifier of the Space member whose role will change.",
        ),
    ],
    body: UpdateSpaceMemberRoleRequest,
    request: Request,
    user_id: UserIdDep,
    service: SpaceMemberServiceProtocol = Injected(SpaceMemberServiceProtocol),
) -> Envelope[SpaceMemberMutationResult]:
    summary = service.update_role(
        space_id=space_id,
        actor_id=user_id,
        user_id=member_user_id,
        role=DomainSpaceRole(body.role),
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
    caller: ActingCallerDep,
    service: MarketFavoriteServiceProtocol = Injected(MarketFavoriteServiceProtocol),
) -> Envelope[FavoriteAddedResult]:
    actor_id = _require_user_delegation(caller)
    record, changed = service.add(
        space_id=space_id,
        actor_id=actor_id,
        market_source=DomainMarketSource(body.market_source),
        target_type=DomainFavoriteTargetType(body.target_type),
        target_code=body.target_code,
    )
    return envelope(
        FavoriteAddedResult(
            favorite_id=record.id,
            market_source=record.market_source,
            target_type=record.target_type,
            target_code=record.target_code,
            changed=changed,
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
    caller: ActingCallerDep,
    service: MarketFavoriteServiceProtocol = Injected(MarketFavoriteServiceProtocol),
) -> Envelope[FavoriteCanceledResult]:
    actor_id = _require_user_delegation(caller)
    changed = service.cancel(
        space_id=space_id,
        actor_id=actor_id,
        market_source=DomainMarketSource(body.market_source),
        target_type=DomainFavoriteTargetType(body.target_type),
        target_code=body.target_code,
    )
    return envelope(
        FavoriteCanceledResult(
            market_source=body.market_source,
            target_type=body.target_type,
            target_code=body.target_code.strip(),
            changed=changed,
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
    caller: ActingCallerDep,
    service: MarketFavoriteServiceProtocol = Injected(MarketFavoriteServiceProtocol),
) -> Envelope[Page[MarketFavoriteItem]]:
    actor_id = _require_user_delegation(caller)
    total, records = service.search(
        space_id=space_id,
        actor_id=actor_id,
        market_source=(
            DomainMarketSource(body.market_source)
            if body.market_source is not None
            else None
        ),
        target_type=(
            DomainFavoriteTargetType(body.target_type)
            if body.target_type is not None
            else None
        ),
        keyword=body.keyword,
        page_no=body.page_no,
        page_size=body.page_size,
    )
    return page(total, [_favorite_item(record) for record in records], request)


@router.post(
    "/{space_id}/market-favorites/status",
    response_model=Envelope[FavoriteStatusesResult],
)
@envelope_errors
async def find_market_favorite_statuses(
    space_id: SpaceIdPath,
    body: FavoriteStatusesRequest,
    request: Request,
    caller: ActingCallerDep,
    service: MarketFavoriteServiceProtocol = Injected(MarketFavoriteServiceProtocol),
) -> Envelope[FavoriteStatusesResult]:
    actor_id = _require_user_delegation(caller)
    target_codes = service.find_favorited_codes(
        space_id=space_id,
        actor_id=actor_id,
        market_source=DomainMarketSource(body.market_source),
        target_type=DomainFavoriteTargetType(body.target_type),
        target_codes=body.target_codes,
    )
    return envelope(
        FavoriteStatusesResult(
            market_source=body.market_source,
            target_type=body.target_type,
            favorited_target_codes=target_codes,
        ),
        request,
    )
