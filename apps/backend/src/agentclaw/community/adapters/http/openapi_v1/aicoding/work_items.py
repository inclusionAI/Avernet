"""Public user-level Agent Coding work-items snapshot.

The card is a self-service view across all Bots the signed human may reach. It
names no single Bot on the path and accepts no caller-selectable ``user_id``:
the acting user is always read from the signed principal. Application-only
callers are refused while this remains a human UI snapshot; delegating it would
first require narrowing the Bot list by the app's live grants.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    caller_owner_id,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.core.aicoding.work_items_models import (
    RUN_STATUS_TOKENS,
    SOURCE_TOKENS,
    WorkItemsData,
    WorkItemsFilterError,
    parse_optional_token_filter,
)
from agentclaw.community.api.work_items_service import (
    AICodingWorkItemsServiceProtocol,
)
from agentclaw.community.di import Injected

PrincipalDep = Annotated[Principal, Depends(require_principal)]
_REFUSES_APP_ONLY = [Depends(refuse_app_only_caller)]

router = APIRouter(
    prefix="/openapi/v1/bots/aicoding",
    tags=["aicoding"],
    route_class=PublicAPIRoute,
    dependencies=_REFUSES_APP_ONLY,
)

_SOURCE_DESCRIPTION = (
    "Optional comma-separated source tokens: mine, others, or collab. "
    "Omit for no source filter."
)
_SINCE_DESCRIPTION = (
    "Optional inclusive lower bound in epoch milliseconds for session update time."
)
_RUN_STATUS_DESCRIPTION = (
    "Optional comma-separated DevFlow status tokens. non_idle means a non-empty "
    "status that is not idle. Omit for no status-token filter; idle and empty "
    "statuses are always excluded from work items."
)
_LIMIT_DESCRIPTION = (
    "Optional per-Bot Relay scan window from 1 to 200. It does not limit the "
    "final aggregate item count. Default: 50."
)


@router.get("/work-items", response_model=Envelope[WorkItemsData])
@envelope_errors
async def get_work_items(
    request: Request,
    principal: PrincipalDep,
    source: Annotated[
        str | None, Query(description=_SOURCE_DESCRIPTION, examples=["mine"])
    ] = None,
    since_ms: Annotated[
        int | None,
        Query(gt=0, description=_SINCE_DESCRIPTION, examples=[1794000000000]),
    ] = None,
    run_status: Annotated[
        str | None,
        Query(
            description=_RUN_STATUS_DESCRIPTION,
            examples=["non_idle", "running,awaitingHuman"],
        ),
    ] = None,
    limit: Annotated[
        int | None,
        Query(ge=1, le=200, description=_LIMIT_DESCRIPTION, examples=[50]),
    ] = None,
    service: AICodingWorkItemsServiceProtocol = Injected(
        AICodingWorkItemsServiceProtocol
    ),
) -> Envelope[WorkItemsData]:
    """Return the signed caller's recent Agent Coding work items across Bots.

    The aggregate is intentionally a snapshot rather than a global page:
    limit is applied independently inside each Bot, partial Bot failures are
    reported in data.bots, and the final data.items list may exceed
    limit.
    """
    try:
        source_filter = parse_optional_token_filter(source, SOURCE_TOKENS, "source")
        status_filter = parse_optional_token_filter(
            run_status, RUN_STATUS_TOKENS, "run_status"
        )
    except WorkItemsFilterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    data = await service.get_work_items(
        current_user_id=caller_owner_id(principal),
        source_filter=source_filter,
        since_ms=since_ms,
        run_status_filter=status_filter,
        scan_limit=limit,
    )
    return envelope(data, request)


__all__ = ["router"]
