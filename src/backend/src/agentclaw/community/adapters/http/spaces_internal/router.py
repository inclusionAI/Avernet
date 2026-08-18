"""Internal Space endpoints used by trusted service integrations."""

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import envelope
from agentclaw.community.adapters.http.spaces_internal.schemas import (
    PersonalSpaceBatchQueryItem,
    PersonalSpaceBatchQueryRequest,
    PersonalSpaceBatchQueryResult,
)
from agentclaw.community.api.space_service import SpaceServiceProtocol
from agentclaw.community.di import Injected


router = APIRouter(prefix="/api/internal/spaces", tags=["spaces-internal"])


@router.post(
    "/personal/batch-query",
    response_model=Envelope[PersonalSpaceBatchQueryResult],
)
async def batch_query_personal_spaces(
    body: PersonalSpaceBatchQueryRequest,
    request: Request,
    service: SpaceServiceProtocol = Injected(SpaceServiceProtocol),
) -> Envelope[PersonalSpaceBatchQueryResult]:
    records = service.batch_query_personal(user_ids=body.user_id)
    return envelope(
        PersonalSpaceBatchQueryResult(
            list=[
                PersonalSpaceBatchQueryItem(
                    user_id=record.user_id,
                    space_id=record.space_id,
                    found=record.found,
                )
                for record in records
            ]
        ),
        request,
    )
