"""OpenAPI v1 adapter for spaces, members and market favorites.

Every user-scoped operation names its acting user through the shared
``user_id`` query dependency. Admission decides whether an application may
reach the operation; Space ownership rules remain in the core services.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from starlette.concurrency import run_in_threadpool

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
from agentclaw.community.adapters.http.openapi_v1.spaces.multipart_limits import (
    SpaceSkillPublicAPIRoute,
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
    InitializePersonalSpaceRequest,
    SpaceListScope,
    SearchFavoritesRequest,
    SpaceCreated,
    SpaceRole,
    SpaceType,
    SpaceItem,
    SpaceMemberDeletedResult,
    SpaceMemberItem,
    SpaceMemberMutationResult,
    DraftFileTree,
    DraftFileContent,
    SaveDraftFileRequest,
    DraftRevisionRequest,
    SkillDraftDetail,
    DraftDeleteResult,
    SkillGrantItem,
    SpaceSkillGrants,
    TransferSkillOwnerRequest,
    CreateSkillEditorRequest,
    SkillEditorRequestCreated,
    DraftEditLeaseResource,
    UpdateSpaceMemberRoleRequest,
)
from agentclaw.community.api.market_favorite_service import (
    MarketFavoriteServiceProtocol,
)
from agentclaw.community.api.space_service import (
    SpaceMemberServiceProtocol,
    SpaceServiceProtocol,
)
from agentclaw.community.api.space_skill_application_service import (
    SpaceSkillApplicationServiceProtocol,
)
from agentclaw.community.api.space_skill_grant_service import (
    SpaceSkillGrantServiceProtocol,
)
from agentclaw.community.api.space_skill_editor_request_service import (
    SpaceSkillEditorRequestServiceProtocol,
)
from agentclaw.community.api.draft_edit_lease_service import (
    DraftEditLeaseServiceProtocol,
)
from agentclaw.community.core.market_favorites.models import (
    FavoriteTargetType as DomainFavoriteTargetType,
    MarketSource as DomainMarketSource,
    MarketFavoriteRecord,
)
from agentclaw.community.core.skill_center.skill_package import (
    MAX_FILE_BYTES,
    SkillPackageInvalidError,
    SkillPackageTooLargeError,
)
from agentclaw.community.core.spaces.models import (
    SpaceListScope as DomainSpaceListScope,
    SpaceMemberSummaryRecord,
    SpaceRole as DomainSpaceRole,
    SpaceSummaryRecord,
    SpaceType as DomainSpaceType,
)
from agentclaw.community.di import Injected

router = APIRouter(
    prefix="/openapi/v1/bots/spaces",
    tags=["spaces"],
    route_class=SpaceSkillPublicAPIRoute,
)
SpaceIdPath = Annotated[int, Path(ge=1, description="Space primary identifier.")]
SkillIdPath = Annotated[int, Path(ge=1, description="Space Skill primary identifier.")]
GrantUserIdPath = Annotated[
    str, Path(min_length=1, max_length=128, description="Grant target user identifier.")
]
PageNoQuery = Annotated[int, Query(ge=1, description="One-based page number.")]
PageSizeQuery = Annotated[
    int, Query(ge=1, le=100, description="Maximum items returned per page.")
]
LeaseFencingTokenQuery = Annotated[
    int,
    Query(
        ge=1,
        description="Exact fencing token returned when this actor acquired the Lease.",
    ),
]
IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        description="Stable identity for one creation intent and its network retries.",
    ),
]
_REFUSES_APP_ONLY = [Depends(refuse_app_only_caller)]
_UTF8_SIZE_CHUNK_CHARS = 64 * 1024


def _require_draft_file_content_size(content: str) -> None:
    encoded_size = 0
    for offset in range(0, len(content), _UTF8_SIZE_CHUNK_CHARS):
        try:
            encoded_size += len(
                content[offset : offset + _UTF8_SIZE_CHUNK_CHARS].encode("utf-8")
            )
        except UnicodeEncodeError as exc:
            raise SkillPackageInvalidError("invalid_encoding") from exc
        if encoded_size > MAX_FILE_BYTES:
            raise SkillPackageTooLargeError()


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
    scope: Annotated[
        SpaceListScope,
        Query(description="Space visibility scope: all or accessible."),
    ] = SpaceListScope.ALL,
    service: SpaceServiceProtocol = Injected(SpaceServiceProtocol),
) -> Envelope[Page[SpaceItem]]:
    actor_id = _require_user_delegation(caller)
    total, records = service.list_spaces(
        user_id=actor_id,
        keyword=keyword,
        space_type=DomainSpaceType(space_type) if space_type is not None else None,
        page_no=page_no,
        page_size=page_size,
        scope=DomainSpaceListScope(scope.value),
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
    body: InitializePersonalSpaceRequest | None = None,
    service: SpaceServiceProtocol = Injected(SpaceServiceProtocol),
) -> Envelope[PersonalSpaceInitialized]:
    create_sc_team = not body.skip_sc if body is not None else True
    record, was_created = service.initialize_personal(
        user_id=user_id, create_sc_team=create_sc_team
    )
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
    record = service.create_team(
        name=body.space_name, creator_id=user_id, create_sc_team=not body.skip_sc
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
    "/{space_id}/skills/{skill_id}/draft/files",
    response_model=Envelope[DraftFileTree],
)
@envelope_errors
async def get_space_skill_draft_file_tree(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    request: Request,
    caller: ActingCallerDep,
    service: SpaceSkillApplicationServiceProtocol = Injected(
        SpaceSkillApplicationServiceProtocol
    ),
) -> Envelope[DraftFileTree]:
    actor_id = _require_user_delegation(caller)
    result = await run_in_threadpool(
        service.get_draft_file_tree,
        space_id=space_id,
        skill_id=skill_id,
        actor_id=actor_id,
    )
    return envelope(DraftFileTree.model_validate(result), request)


@router.get(
    "/{space_id}/skills/{skill_id}/draft/files/{path:path}",
    response_model=Envelope[DraftFileContent],
)
@envelope_errors
async def read_space_skill_draft_file(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    path: Annotated[
        str, Path(description="Normalized POSIX-relative Draft file path.")
    ],
    request: Request,
    caller: ActingCallerDep,
    service: SpaceSkillApplicationServiceProtocol = Injected(
        SpaceSkillApplicationServiceProtocol
    ),
) -> Envelope[DraftFileContent]:
    actor_id = _require_user_delegation(caller)
    result = await run_in_threadpool(
        service.read_draft_file,
        space_id=space_id,
        skill_id=skill_id,
        actor_id=actor_id,
        path=path,
    )
    return envelope(DraftFileContent.model_validate(result), request)


@router.put(
    "/{space_id}/skills/{skill_id}/draft/files/{path:path}",
    response_model=Envelope[SkillDraftDetail],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def save_space_skill_draft_file(
    body: SaveDraftFileRequest,
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    path: Annotated[
        str, Path(description="Normalized POSIX-relative Draft file path.")
    ],
    request: Request,
    user_id: UserIdDep,
    service: SpaceSkillApplicationServiceProtocol = Injected(
        SpaceSkillApplicationServiceProtocol
    ),
) -> Envelope[SkillDraftDetail]:
    _require_draft_file_content_size(body.content)
    result = await run_in_threadpool(
        service.save_draft_file,
        space_id=space_id,
        skill_id=skill_id,
        actor_id=user_id,
        path=path,
        content=body.content,
        expected_revision_id=body.expected_revision_id,
        fencing_token=body.fencing_token,
    )
    return envelope(SkillDraftDetail.model_validate(result), request)


@router.post(
    "/{space_id}/skills/{skill_id}/draft/upgrade",
    status_code=201,
    response_model=Envelope[SkillDraftDetail],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def create_space_skill_upgrade_draft(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    request: Request,
    user_id: UserIdDep,
    idempotency_key: IdempotencyKeyHeader,
    service: SpaceSkillApplicationServiceProtocol = Injected(
        SpaceSkillApplicationServiceProtocol
    ),
) -> Envelope[SkillDraftDetail]:
    result = await run_in_threadpool(
        service.create_upgrade_draft,
        space_id=space_id,
        skill_id=skill_id,
        actor_id=user_id,
        request_id=idempotency_key,
    )
    return created(SkillDraftDetail.model_validate(result), request)


@router.post(
    "/{space_id}/skills/{skill_id}/draft/refresh-from-git",
    response_model=Envelope[SkillDraftDetail],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def refresh_space_skill_draft_from_git(
    body: DraftRevisionRequest,
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    request: Request,
    user_id: UserIdDep,
    service: SpaceSkillApplicationServiceProtocol = Injected(
        SpaceSkillApplicationServiceProtocol
    ),
) -> Envelope[SkillDraftDetail]:
    result = await run_in_threadpool(
        service.refresh_draft_from_git,
        space_id=space_id,
        skill_id=skill_id,
        actor_id=user_id,
        expected_revision_id=body.expected_revision_id,
        fencing_token=body.fencing_token,
    )
    return envelope(SkillDraftDetail.model_validate(result), request)


@router.delete(
    "/{space_id}/skills/{skill_id}/draft",
    response_model=Envelope[DraftDeleteResult],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def delete_space_skill_draft(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    request: Request,
    user_id: UserIdDep,
    expected_revision_id: Annotated[
        str,
        Query(
            min_length=1,
            max_length=128,
            description="Revision the caller expects to delete.",
        ),
    ],
    fencing_token: Annotated[
        int | None,
        Query(ge=1, description="Current Team Lease token; omit for Personal Space."),
    ] = None,
    service: SpaceSkillApplicationServiceProtocol = Injected(
        SpaceSkillApplicationServiceProtocol
    ),
) -> Envelope[DraftDeleteResult]:
    result = await run_in_threadpool(
        service.delete_draft,
        space_id=space_id,
        skill_id=skill_id,
        actor_id=user_id,
        expected_revision_id=expected_revision_id,
        fencing_token=fencing_token,
    )
    return envelope(DraftDeleteResult.model_validate(result), request)


@router.get(
    "/{space_id}/skills/{skill_id}/grants",
    response_model=Envelope[SpaceSkillGrants],
)
@envelope_errors
async def list_space_skill_grants(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    request: Request,
    caller: ActingCallerDep,
    service: SpaceSkillGrantServiceProtocol = Injected(SpaceSkillGrantServiceProtocol),
) -> Envelope[SpaceSkillGrants]:
    actor_id = _require_user_delegation(caller)
    return envelope(
        SpaceSkillGrants.model_validate(
            service.list_grants(space_id=space_id, skill_id=skill_id, actor_id=actor_id)
        ),
        request,
    )


@router.put(
    "/{space_id}/skills/{skill_id}/managers/{manager_user_id}",
    response_model=Envelope[SkillGrantItem],
    response_model_exclude_none=True,
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def add_space_skill_manager(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    manager_user_id: GrantUserIdPath,
    request: Request,
    user_id: UserIdDep,
    service: SpaceSkillGrantServiceProtocol = Injected(SpaceSkillGrantServiceProtocol),
) -> Envelope[SkillGrantItem]:
    result = service.add_manager(
        space_id=space_id,
        skill_id=skill_id,
        actor_id=user_id,
        manager_user_id=manager_user_id,
    )
    return envelope(SkillGrantItem.model_validate(result), request)


@router.delete(
    "/{space_id}/skills/{skill_id}/managers/{manager_user_id}",
    response_model=Envelope[SkillGrantItem],
    response_model_exclude_none=True,
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def remove_space_skill_manager(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    manager_user_id: GrantUserIdPath,
    request: Request,
    user_id: UserIdDep,
    service: SpaceSkillGrantServiceProtocol = Injected(SpaceSkillGrantServiceProtocol),
) -> Envelope[SkillGrantItem]:
    result = service.remove_manager(
        space_id=space_id,
        skill_id=skill_id,
        actor_id=user_id,
        manager_user_id=manager_user_id,
    )
    return envelope(SkillGrantItem.model_validate(result), request)


@router.post(
    "/{space_id}/skills/{skill_id}/owner-transfer",
    response_model=Envelope[SpaceSkillGrants],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def transfer_space_skill_owner(
    body: TransferSkillOwnerRequest,
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    request: Request,
    user_id: UserIdDep,
    service: SpaceSkillGrantServiceProtocol = Injected(SpaceSkillGrantServiceProtocol),
) -> Envelope[SpaceSkillGrants]:
    result = service.transfer_owner(
        space_id=space_id,
        skill_id=skill_id,
        actor_id=user_id,
        new_owner_user_id=body.new_owner_user_id,
        reason=body.reason,
        retain_previous_owner_as_manager=body.retain_previous_owner_as_manager,
    )
    return envelope(SpaceSkillGrants.model_validate(result), request)


@router.get(
    "/{space_id}/skills/{skill_id}/draft/lease",
    response_model=Envelope[DraftEditLeaseResource],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def get_draft_edit_lease(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    request: Request,
    user_id: UserIdDep,
    service: DraftEditLeaseServiceProtocol = Injected(DraftEditLeaseServiceProtocol),
) -> Envelope[DraftEditLeaseResource]:
    result = service.get_lease(space_id=space_id, skill_id=skill_id, actor_id=user_id)
    return envelope(DraftEditLeaseResource.model_validate(result), request)


@router.put(
    "/{space_id}/skills/{skill_id}/draft/lease",
    response_model=Envelope[DraftEditLeaseResource],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def acquire_draft_edit_lease(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    request: Request,
    user_id: UserIdDep,
    service: DraftEditLeaseServiceProtocol = Injected(DraftEditLeaseServiceProtocol),
) -> Envelope[DraftEditLeaseResource]:
    result = service.acquire(space_id=space_id, skill_id=skill_id, actor_id=user_id)
    return envelope(DraftEditLeaseResource.model_validate(result), request)


@router.delete(
    "/{space_id}/skills/{skill_id}/draft/lease",
    response_model=Envelope[DraftEditLeaseResource],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def release_draft_edit_lease(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    fencing_token: LeaseFencingTokenQuery,
    request: Request,
    user_id: UserIdDep,
    service: DraftEditLeaseServiceProtocol = Injected(DraftEditLeaseServiceProtocol),
) -> Envelope[DraftEditLeaseResource]:
    result = service.release(
        space_id=space_id,
        skill_id=skill_id,
        actor_id=user_id,
        fencing_token=fencing_token,
    )
    return envelope(DraftEditLeaseResource.model_validate(result), request)


@router.post(
    "/{space_id}/skills/{skill_id}/draft/lease/takeover",
    response_model=Envelope[DraftEditLeaseResource],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def takeover_draft_edit_lease(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    request: Request,
    user_id: UserIdDep,
    service: DraftEditLeaseServiceProtocol = Injected(DraftEditLeaseServiceProtocol),
) -> Envelope[DraftEditLeaseResource]:
    result = service.takeover(space_id=space_id, skill_id=skill_id, actor_id=user_id)
    return envelope(DraftEditLeaseResource.model_validate(result), request)


@router.post(
    "/{space_id}/skills/{skill_id}/editor-requests",
    status_code=201,
    response_model=Envelope[SkillEditorRequestCreated],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def create_space_skill_editor_request(
    body: CreateSkillEditorRequest,
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    request: Request,
    user_id: UserIdDep,
    service: SpaceSkillEditorRequestServiceProtocol = Injected(
        SpaceSkillEditorRequestServiceProtocol
    ),
) -> Envelope[SkillEditorRequestCreated]:
    result = service.create_request(
        space_id=space_id,
        skill_id=skill_id,
        applicant_user_id=user_id,
        reason=body.reason,
    )
    return created(
        SkillEditorRequestCreated(
            work_order_id=result.id,
            work_order_no=result.work_order_no,
            status=result.status,
        ),
        request,
    )


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
