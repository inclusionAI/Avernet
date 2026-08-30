"""Thin HTTP adapter for durable SC Public Reference operations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope, Page
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import OwnerIdDep
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors, page
from agentclaw.community.api.skill_center_reference_service import (
    SkillCenterReferenceItem,
    SkillCenterReferenceServiceProtocol,
    SkillCenterReferenceStatus,
)
from agentclaw.community.di import Injected


router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references",
    tags=["skill-center-references"],
    route_class=PublicAPIRoute,
)
SetIdPath = Annotated[str, Path(description="Decimal SkillSet identifier.")]
ReferenceIdPath = Annotated[str, Path(description="Durable Reference item identifier.")]
IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=190,
        description="Required command idempotency key.",
    ),
]


class CreateSkillCenterReferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_codes: list[str] = Field(min_length=1)


class CreateSkillCenterReferencesResponse(BaseModel):
    request_id: str
    reference_ids: list[str]


class SkillCenterReferenceResponse(BaseModel):
    reference_id: str
    request_id: str
    skill_set_id: str
    skill_code: str
    sc_version_number: str | None
    status: SkillCenterReferenceStatus
    skill_id: str | None
    error_code: str | None
    error_message: str | None
    gmt_created: datetime
    gmt_modified: datetime

    @field_serializer("gmt_created", "gmt_modified")
    def serialize_utc(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _item(item: SkillCenterReferenceItem) -> SkillCenterReferenceResponse:
    return SkillCenterReferenceResponse(
        reference_id=item.reference_id,
        request_id=item.request_id,
        skill_set_id=item.skill_set_id,
        skill_code=item.skill_code,
        sc_version_number=item.sc_version_number,
        status=item.status,
        skill_id=item.skill_id,
        error_code=item.error_code,
        error_message=item.error_message,
        gmt_created=item.gmt_created,
        gmt_modified=item.gmt_modified,
    )


@router.post(
    "",
    response_model=Envelope[CreateSkillCenterReferencesResponse],
    status_code=202,
)
@envelope_errors
async def create_skill_center_references(
    bot_id: BotIdPath,
    set_id: SetIdPath,
    body: CreateSkillCenterReferencesRequest,
    idempotency_key: IdempotencyKeyHeader,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    response: Response,
    service: SkillCenterReferenceServiceProtocol = Injected(
        SkillCenterReferenceServiceProtocol
    ),
) -> Envelope[CreateSkillCenterReferencesResponse]:
    batch = service.create(
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=user_id,
        skill_set_id=set_id,
        idempotency_key=idempotency_key,
        skill_codes=tuple(body.skill_codes),
    )
    response.status_code = 202
    return envelope(
        CreateSkillCenterReferencesResponse(
            request_id=batch.request_id,
            reference_ids=[item.reference_id for item in batch.items],
        ),
        request,
        code=202000,
    )


@router.get("", response_model=Envelope[Page[SkillCenterReferenceResponse]])
@envelope_errors
async def list_skill_center_references(
    bot_id: BotIdPath,
    set_id: SetIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    request_id: str | None = Query(default=None),
    status: SkillCenterReferenceStatus | None = Query(default=None),
    page_number: int = Query(default=1, alias="page", ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: SkillCenterReferenceServiceProtocol = Injected(
        SkillCenterReferenceServiceProtocol
    ),
) -> Envelope[Page[SkillCenterReferenceResponse]]:
    del user_id
    result = service.list(
        bot_id=bot_id,
        owner_id=owner_id,
        skill_set_id=set_id,
        request_id=request_id,
        status=status,
        page=page_number,
        page_size=page_size,
    )
    return page(result.total, [_item(item) for item in result.items], request)


@router.get(
    "/{reference_id}", response_model=Envelope[SkillCenterReferenceResponse]
)
@envelope_errors
async def get_skill_center_reference(
    bot_id: BotIdPath,
    set_id: SetIdPath,
    reference_id: ReferenceIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    request: Request,
    service: SkillCenterReferenceServiceProtocol = Injected(
        SkillCenterReferenceServiceProtocol
    ),
) -> Envelope[SkillCenterReferenceResponse]:
    del user_id
    item = service.get(
        bot_id=bot_id,
        owner_id=owner_id,
        skill_set_id=set_id,
        reference_id=reference_id,
    )
    return envelope(_item(item), request)


__all__ = ["router"]
