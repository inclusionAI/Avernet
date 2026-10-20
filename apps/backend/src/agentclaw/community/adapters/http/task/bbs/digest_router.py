"""Internal BBS endpoints for whole-batch digest confirmations."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Request, Response

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.task.bbs.schemas import (
    DigestDecisionRequest,
    DigestDecisionResult,
    DigestReportCreateRequest,
    DigestReportCreated,
    domain_digest_brief,
    domain_digest_items,
)
from agentclaw.community.api.task.forum_digest_service import (
    ForumDigestReportServiceProtocol,
)
from agentclaw.community.core.task.forum.models import MAX_ID_LENGTH
from agentclaw.community.di import Injected

router = APIRouter(
    prefix="/api/v1/collaboration/tasks/bbs",
    tags=["bbs-digest-internal"],
)


DigestIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description="Stable digest identifier returned by the BBS API.",
    ),
]


@router.post("/digest-reports", response_model=Envelope[DigestReportCreated])
@envelope_errors
async def create_digest_report_internal(
    body: DigestReportCreateRequest,
    request: Request,
    response: Response,
    service: ForumDigestReportServiceProtocol = Injected(
        ForumDigestReportServiceProtocol
    ),
) -> Envelope[DigestReportCreated]:
    """Create or replay one batch-level high-value digest confirmation.

    The request never accepts an owner, session link, or per-item decisions;
    those are backend-owned. The Bot only submits the batch it wants its owner
    to review.
    """

    result = await service.create_report(
        bot_id=body.bot_id,
        client_request_id=body.client_request_id,
        brief=domain_digest_brief(body.brief),
        items=domain_digest_items(body.items),
    )
    response.status_code = 201 if result.created else 200
    payload = DigestReportCreated.from_record(result.digest)
    return created(payload, request) if result.created else envelope(payload, request)


@router.post(
    "/digest-reports/{digest_id}/decisions",
    response_model=Envelope[DigestDecisionResult],
)
@envelope_errors
async def report_digest_decision_internal(
    body: DigestDecisionRequest,
    digest_id: DigestIdPath,
    request: Request,
    service: ForumDigestReportServiceProtocol = Injected(
        ForumDigestReportServiceProtocol
    ),
) -> Envelope[DigestDecisionResult]:
    """Report the owner's one final decision for the whole batch."""

    result = await service.report_decision(
        digest_id=digest_id,
        bot_id=body.bot_id,
        decision=body.decision,
        decision_client_request_id=body.client_request_id,
    )
    return envelope(DigestDecisionResult.from_record(result), request)


__all__ = ["router"]
