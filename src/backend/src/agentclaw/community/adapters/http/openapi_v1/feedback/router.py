"""Public feedback collection: submit and list general user feedback."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.core.feedback.models import (
    MAX_MODULE_LENGTH,
    MAX_REPORTER_ID_LENGTH,
)
from agentclaw.community.core.feedback.service_protocol import FeedbackServiceProtocol
from agentclaw.community.di import Injected

from .schemas import CreateFeedbackRequest, FeedbackCreated, FeedbackListItem

read_router = APIRouter(
    prefix="/openapi/v1/feedback",
    tags=["feedback"],
    route_class=PublicAPIRoute,
)


@read_router.post(
    "",
    response_model=Envelope[FeedbackCreated],
    # Authorised on the route (any verified principal) so the principal-seam
    # test sees it; the reporter is declared in the body, not the caller.
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def create_feedback(
    body: CreateFeedbackRequest,
    request: Request,
    response: Response,
    service: FeedbackServiceProtocol = Injected(FeedbackServiceProtocol),
) -> Envelope[FeedbackCreated]:
    """Submit one feedback entry authored by the reporter in the body."""
    result = service.create_feedback(
        reporter_id=body.reporter_id,
        module=body.module,
        content=body.content,
    )
    response.status_code = 201
    return created(FeedbackCreated(id=result.feedback.id), request)


@read_router.get(
    "",
    response_model=Envelope[Page[FeedbackListItem]],
    # Authorised on the route (any verified principal) so the principal-seam
    # test sees it; filters are caller-selected and name no addressed bot.
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def list_feedback(
    request: Request,
    page_params: PageParamsDep,
    module: str | None = Query(
        default=None,
        max_length=MAX_MODULE_LENGTH,
        description="Optional feedback module filter, e.g. bbs / task.",
    ),
    reporter_id: str | None = Query(
        default=None,
        max_length=MAX_REPORTER_ID_LENGTH,
        description="Optional reporter filter (exact match).",
    ),
    service: FeedbackServiceProtocol = Injected(FeedbackServiceProtocol),
) -> Envelope[Page[FeedbackListItem]]:
    """Page feedback in the caller's tenant and environment, newest first."""
    result = service.list_feedback(
        module=module,
        reporter_id=reporter_id,
        page=page_params.page,
        page_size=page_params.page_size,
    )
    return page_envelope(
        result.total,
        [FeedbackListItem.from_record(item) for item in result.items],
        request,
    )
