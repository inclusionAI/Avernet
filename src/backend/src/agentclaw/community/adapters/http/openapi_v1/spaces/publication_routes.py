"""Public Space Skill Publication impact, Attempt, and recovery routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope, Page
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    accepted,
    envelope,
    envelope_errors,
    page,
)
from agentclaw.community.adapters.http.openapi_v1.spaces.multipart_limits import (
    SpaceSkillPublicAPIRoute,
)
from agentclaw.community.adapters.http.openapi_v1.spaces.schemas import (
    PublicationAttempt,
    PublicationImpactItem,
)
from agentclaw.community.api.space_skill_publication_service import (
    SpaceSkillPublicationServiceProtocol,
)
from agentclaw.community.di import Injected


router = APIRouter(
    prefix="/openapi/v1/bots/spaces",
    tags=["space-skill-publications"],
    route_class=SpaceSkillPublicAPIRoute,
)
SpaceIdPath = Annotated[int, Path(ge=1)]
SkillIdPath = Annotated[int, Path(ge=1)]
AttemptIdPath = Annotated[int, Path(ge=1)]
PageSizeQuery = Annotated[int, Query(ge=1, le=100)]
IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]
_REFUSES_APP_ONLY = [Depends(refuse_app_only_caller)]


def _attempt(record) -> PublicationAttempt:
    return PublicationAttempt.model_validate(
        {
            "attempt_id": str(record.attempt_id),
            "target_version": record.target_version,
            "status": record.status,
            "sc_version_number": record.sc_version_number,
            "recovery": record.recovery,
            "error_code": record.error_code,
            "error_message": record.error_message,
            "gmt_created": record.gmt_created,
            "gmt_modified": record.gmt_modified,
        },
        from_attributes=True,
    )


@router.get(
    "/{space_id}/skills/{skill_id}/publication-impact",
    response_model=Envelope[Page[PublicationImpactItem]],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def get_publication_impact(
    request: Request,
    user_id: UserIdDep,
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    page_number: Annotated[int, Query(alias="page", ge=1)] = 1,
    page_size: PageSizeQuery = 20,
    service: SpaceSkillPublicationServiceProtocol = Injected(
        SpaceSkillPublicationServiceProtocol
    ),
) -> Envelope[Page[PublicationImpactItem]]:
    total, records = await run_in_threadpool(
        service.list_publication_impact,
        space_id=space_id,
        skill_id=skill_id,
        actor_id=user_id,
        page=page_number,
        page_size=page_size,
    )
    return page(
        total,
        [
            PublicationImpactItem.model_validate(record, from_attributes=True)
            for record in records
        ],
        request,
    )


@router.post(
    "/{space_id}/skills/{skill_id}/publications",
    status_code=202,
    response_model=Envelope[PublicationAttempt],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def create_publication(
    request: Request,
    user_id: UserIdDep,
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    idempotency_key: IdempotencyKeyHeader,
    service: SpaceSkillPublicationServiceProtocol = Injected(
        SpaceSkillPublicationServiceProtocol
    ),
) -> Envelope[PublicationAttempt]:
    record = await run_in_threadpool(
        service.create_publication,
        space_id=space_id,
        skill_id=skill_id,
        actor_id=user_id,
        request_id=idempotency_key,
    )
    return accepted(_attempt(record), request)


@router.get(
    "/{space_id}/skills/{skill_id}/publications",
    response_model=Envelope[Page[PublicationAttempt]],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def list_publications(
    request: Request,
    user_id: UserIdDep,
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    page_number: Annotated[int, Query(alias="page", ge=1)] = 1,
    page_size: PageSizeQuery = 20,
    service: SpaceSkillPublicationServiceProtocol = Injected(
        SpaceSkillPublicationServiceProtocol
    ),
) -> Envelope[Page[PublicationAttempt]]:
    total, records = await run_in_threadpool(
        service.list_publications,
        space_id=space_id,
        skill_id=skill_id,
        actor_id=user_id,
        page=page_number,
        page_size=page_size,
    )
    return page(total, [_attempt(record) for record in records], request)


@router.get(
    "/{space_id}/skills/{skill_id}/publications/{attempt_id}",
    response_model=Envelope[PublicationAttempt],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def get_publication(
    request: Request,
    user_id: UserIdDep,
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    attempt_id: AttemptIdPath,
    service: SpaceSkillPublicationServiceProtocol = Injected(
        SpaceSkillPublicationServiceProtocol
    ),
) -> Envelope[PublicationAttempt]:
    record = await run_in_threadpool(
        service.get_publication,
        space_id=space_id,
        skill_id=skill_id,
        attempt_id=attempt_id,
        actor_id=user_id,
    )
    return envelope(_attempt(record), request)


@router.post(
    "/{space_id}/skills/{skill_id}/publications/{attempt_id}/retry",
    status_code=202,
    response_model=Envelope[PublicationAttempt],
    responses={
        200: {
            "model": Envelope[PublicationAttempt],
            "description": "The Attempt had already succeeded; no task was needed.",
        }
    },
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def retry_publication(
    response: Response,
    request: Request,
    user_id: UserIdDep,
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    attempt_id: AttemptIdPath,
    service: SpaceSkillPublicationServiceProtocol = Injected(
        SpaceSkillPublicationServiceProtocol
    ),
) -> Envelope[PublicationAttempt]:
    result = await run_in_threadpool(
        service.retry_publication,
        space_id=space_id,
        skill_id=skill_id,
        attempt_id=attempt_id,
        actor_id=user_id,
    )
    if result.task_required:
        return accepted(_attempt(result.attempt), request)
    response.status_code = 200
    return envelope(_attempt(result.attempt), request)


__all__ = ["router"]
