"""Internal feedback collection: the ``/api/v1/feedback`` surface.

Mirrors ``openapi_v1/feedback/router.py`` verbatim — same body schema, same
query params, same ``FeedbackServiceProtocol`` delegation, same
``Envelope``/``Page`` builders and the same ``@envelope_errors`` decorator.
Only two seams differ from the public router, by design:

* ``prefix`` is ``/api/v1/feedback`` (not ``/openapi/v1/feedback``) so trusted
  Agent / app-to-app backend callers reach it through the internal surface
  alongside ``/api/v1/bbs``; and
* the route drops the public-surface ``route_class=PublicAPIRoute`` and the
  ``Depends(require_principal)`` gateway seam — internal callers bypass the
  gateway admission/grant layer exactly as the BBS internal router does.

Both surfaces still share ``FeedbackServiceProtocol``, so persistence,
validation and pagination semantics cannot drift; the only divergence is the
prefix and the principal seam — exactly the contract the BBS spine enforces.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.feedback.schemas import (
    CreateFeedbackRequest,
    FeedbackCreated,
    FeedbackListItem,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.core.feedback.models import (
    MAX_MODULE_LENGTH,
    MAX_REPORTER_ID_LENGTH,
)
from agentclaw.community.core.feedback.service_protocol import (
    FeedbackServiceProtocol,
)
from agentclaw.community.di import Injected

read_router = APIRouter(
    prefix="/api/v1/feedback",
    tags=["feedback-internal"],
)


@read_router.post(
    "",
    response_model=Envelope[FeedbackCreated],
)
@envelope_errors
async def create_feedback(
    body: CreateFeedbackRequest,
    request: Request,
    response: Response,
    service: FeedbackServiceProtocol = Injected(FeedbackServiceProtocol),
) -> Envelope[FeedbackCreated]:
    """Submit one feedback entry authored by the reporter declared in the body.

    Internal mirror of ``POST /openapi/v1/feedback``: same body schema, same
    ``FeedbackServiceProtocol.create_feedback`` call, same ``201 + Envelope[
    FeedbackCreated]`` response; the internal route drops the public-surface
    principal seam. Feedback has no idempotency key, so every call writes a
    new row.
    """
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
    """Page feedback in the current tenant and environment, newest first.

    Internal mirror of ``GET /openapi/v1/feedback``: same query params, same
    ``FeedbackServiceProtocol.list_feedback`` call, same
    ``Envelope[Page[FeedbackListItem]]`` response. The internal route drops the
    public-surface principal seam; filters remain caller-selected and name no
    addressed bot.
    """
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
