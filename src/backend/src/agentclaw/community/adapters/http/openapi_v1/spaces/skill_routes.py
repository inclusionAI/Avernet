"""Public Space Skill list, creation, detail and Published Version routes."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Form,
    Header,
    Path,
    Query,
    Request,
)

from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
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
    ConsumableSpaceSkill,
    ImportSpaceSkillFromGitRequest,
    PublishedVersionFileContent,
    PublishedVersionFileTree,
    SkillVersionDetail,
    SpaceSkillFolderUpload,
    SpaceSkillDetail,
    SpaceSkillSummary,
)
from agentclaw.community.api.space_skill_application_service import (
    SpaceSkillApplicationServiceProtocol,
)
from agentclaw.community.api.space_skill_query_service import (
    SpaceSkillSummaryRecord,
    SpaceSkillQueryServiceProtocol,
)
from agentclaw.community.api.space_skill_version_query_service import (
    SpaceSkillVersionQueryServiceProtocol,
)
from agentclaw.community.core.skill_center.skill_package import SkillPackageInvalidError
from agentclaw.community.di import Injected


router = APIRouter(
    prefix="/openapi/v1/bots/spaces",
    tags=["space-skills"],
    route_class=PublicAPIRoute,
)
SpaceIdPath = Annotated[int, Path(ge=1, description="Space primary identifier.")]
SkillIdPath = Annotated[int, Path(ge=1, description="Space Skill primary identifier.")]
PageSizeQuery = Annotated[
    int, Query(ge=1, le=100, description="Number of records per page.")
]
IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        description="Stable identity for replaying this creation command.",
    ),
]
VersionPath = Annotated[int, Path(ge=1, description="Business version ordinal.")]
PublishedFilePath = Annotated[
    str, Path(description="Normalized POSIX-relative Published Version file path.")
]
_REFUSES_APP_ONLY = [Depends(refuse_app_only_caller)]


def _require_user_delegation(caller: ActingCaller) -> str:
    granted = caller.granted_bot_ids()
    if granted is not None and not granted:
        raise GrantNotResolvableError(
            "application holds no live delegation from the named user"
        )
    return caller.user_id


def _space_skill_summary(record: SpaceSkillSummaryRecord) -> SpaceSkillSummary:
    return SpaceSkillSummary.model_validate({**record, "skill_id": str(record["id"])})


def _space_skill_detail(record) -> SpaceSkillDetail:
    return SpaceSkillDetail.model_validate({**record, "skill_id": str(record["id"])})


@router.get(
    "/{space_id}/skills",
    response_model=Envelope[Page[SpaceSkillSummary]],
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
    page_number: Annotated[
        int, Query(alias="page", ge=1, description="One-based page number.")
    ] = 1,
    page_size: PageSizeQuery = 20,
    service: SpaceSkillQueryServiceProtocol = Injected(SpaceSkillQueryServiceProtocol),
) -> Envelope[Page[SpaceSkillSummary]]:
    actor_id = _require_user_delegation(caller)
    total, records = service.list_space_skills(
        space_id=space_id,
        actor_id=actor_id,
        keyword=keyword,
        page=page_number,
        page_size=page_size,
    )
    return page(total, [_space_skill_summary(record) for record in records], request)


@router.post(
    "/{space_id}/skills",
    status_code=201,
    response_model=Envelope[SpaceSkillDetail],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def create_space_skill_from_folder(
    space_id: SpaceIdPath,
    request: Request,
    user_id: UserIdDep,
    idempotency_key: IdempotencyKeyHeader,
    upload: Annotated[SpaceSkillFolderUpload, Form(media_type="multipart/form-data")],
    commands: SpaceSkillApplicationServiceProtocol = Injected(
        SpaceSkillApplicationServiceProtocol
    ),
    queries: SpaceSkillQueryServiceProtocol = Injected(SpaceSkillQueryServiceProtocol),
) -> Envelope[SpaceSkillDetail]:
    try:
        paths = json.loads(upload.file_paths)
    except json.JSONDecodeError as exc:
        raise SkillPackageInvalidError("invalid_file_paths") from exc
    if (
        not isinstance(paths, list)
        or len(paths) != len(upload.files)
        or any(not isinstance(path, str) for path in paths)
    ):
        raise SkillPackageInvalidError("invalid_file_paths")
    files = [
        (path, await uploaded.read())
        for path, uploaded in zip(paths, upload.files, strict=True)
    ]
    outcome = commands.create_from_folder(
        space_id=space_id,
        actor_id=user_id,
        request_id=idempotency_key,
        files=files,
    )
    detail = queries.get_space_skill(
        space_id=space_id, skill_id=outcome.skill_id, actor_id=user_id
    )
    return created(_space_skill_detail(detail), request)


@router.post(
    "/{space_id}/skills/import-from-git",
    status_code=201,
    response_model=Envelope[SpaceSkillDetail],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def import_space_skill_from_git(
    body: ImportSpaceSkillFromGitRequest,
    space_id: SpaceIdPath,
    request: Request,
    user_id: UserIdDep,
    idempotency_key: IdempotencyKeyHeader,
    commands: SpaceSkillApplicationServiceProtocol = Injected(
        SpaceSkillApplicationServiceProtocol
    ),
    queries: SpaceSkillQueryServiceProtocol = Injected(SpaceSkillQueryServiceProtocol),
) -> Envelope[SpaceSkillDetail]:
    outcome = commands.create_from_git(
        space_id=space_id,
        actor_id=user_id,
        request_id=idempotency_key,
        git_url=body.git_url,
        branch=body.branch,
        subdir=body.subdir,
    )
    detail = queries.get_space_skill(
        space_id=space_id, skill_id=outcome.skill_id, actor_id=user_id
    )
    return created(_space_skill_detail(detail), request)


@router.get(
    "/{space_id}/skills/consumable",
    response_model=Envelope[Page[ConsumableSpaceSkill]],
)
@envelope_errors
async def list_consumable_space_skills(
    space_id: SpaceIdPath,
    request: Request,
    caller: ActingCallerDep,
    keyword: Annotated[
        str | None,
        Query(max_length=128, description="Optional name or description search text."),
    ] = None,
    page_number: Annotated[
        int, Query(alias="page", ge=1, description="One-based page number.")
    ] = 1,
    page_size: PageSizeQuery = 20,
    service: SpaceSkillVersionQueryServiceProtocol = Injected(
        SpaceSkillVersionQueryServiceProtocol
    ),
) -> Envelope[Page[ConsumableSpaceSkill]]:
    actor_id = _require_user_delegation(caller)
    total, records = service.list_consumable(
        space_id=space_id,
        actor_id=actor_id,
        keyword=keyword,
        page=page_number,
        page_size=page_size,
    )
    return page(
        total,
        [ConsumableSpaceSkill.model_validate(record) for record in records],
        request,
    )


@router.get(
    "/{space_id}/skills/{skill_id}",
    response_model=Envelope[SpaceSkillDetail],
)
@envelope_errors
async def get_space_skill_detail(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    request: Request,
    caller: ActingCallerDep,
    service: SpaceSkillQueryServiceProtocol = Injected(SpaceSkillQueryServiceProtocol),
) -> Envelope[SpaceSkillDetail]:
    actor_id = _require_user_delegation(caller)
    return envelope(
        _space_skill_detail(
            service.get_space_skill(
                space_id=space_id, skill_id=skill_id, actor_id=actor_id
            )
        ),
        request,
    )


@router.get(
    "/{space_id}/skills/{skill_id}/versions",
    response_model=Envelope[Page[SkillVersionDetail]],
)
@envelope_errors
async def list_space_skill_versions(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    request: Request,
    caller: ActingCallerDep,
    page_number: Annotated[
        int, Query(alias="page", ge=1, description="One-based page number.")
    ] = 1,
    page_size: PageSizeQuery = 20,
    service: SpaceSkillVersionQueryServiceProtocol = Injected(
        SpaceSkillVersionQueryServiceProtocol
    ),
) -> Envelope[Page[SkillVersionDetail]]:
    actor_id = _require_user_delegation(caller)
    total, records = service.list_versions(
        space_id=space_id,
        skill_id=skill_id,
        actor_id=actor_id,
        page=page_number,
        page_size=page_size,
    )
    return page(
        total,
        [SkillVersionDetail.model_validate(record) for record in records],
        request,
    )


@router.get(
    "/{space_id}/skills/{skill_id}/versions/{version}",
    response_model=Envelope[SkillVersionDetail],
)
@envelope_errors
async def get_space_skill_version(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    version: VersionPath,
    request: Request,
    caller: ActingCallerDep,
    service: SpaceSkillVersionQueryServiceProtocol = Injected(
        SpaceSkillVersionQueryServiceProtocol
    ),
) -> Envelope[SkillVersionDetail]:
    actor_id = _require_user_delegation(caller)
    return envelope(
        SkillVersionDetail.model_validate(
            service.get_version(
                space_id=space_id,
                skill_id=skill_id,
                version=version,
                actor_id=actor_id,
            )
        ),
        request,
    )


@router.get(
    "/{space_id}/skills/{skill_id}/versions/{version}/files",
    response_model=Envelope[PublishedVersionFileTree],
)
@envelope_errors
async def get_space_skill_version_file_tree(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    version: VersionPath,
    request: Request,
    caller: ActingCallerDep,
    service: SpaceSkillVersionQueryServiceProtocol = Injected(
        SpaceSkillVersionQueryServiceProtocol
    ),
) -> Envelope[PublishedVersionFileTree]:
    actor_id = _require_user_delegation(caller)
    return envelope(
        PublishedVersionFileTree.model_validate(
            service.get_version_file_tree(
                space_id=space_id,
                skill_id=skill_id,
                version=version,
                actor_id=actor_id,
            )
        ),
        request,
    )


@router.get(
    "/{space_id}/skills/{skill_id}/versions/{version}/files/{path:path}",
    response_model=Envelope[PublishedVersionFileContent],
)
@envelope_errors
async def read_space_skill_version_file(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    version: VersionPath,
    path: PublishedFilePath,
    request: Request,
    caller: ActingCallerDep,
    service: SpaceSkillVersionQueryServiceProtocol = Injected(
        SpaceSkillVersionQueryServiceProtocol
    ),
) -> Envelope[PublishedVersionFileContent]:
    actor_id = _require_user_delegation(caller)
    return envelope(
        PublishedVersionFileContent.model_validate(
            service.read_version_file(
                space_id=space_id,
                skill_id=skill_id,
                version=version,
                actor_id=actor_id,
                path=path,
            )
        ),
        request,
    )
