"""``GET /openapi/v1/bots/{bot_id}/overview`` — non-user Bot overview.

**Who may call.** The gateway fronts this operation with an auth strategy that
needs no user credential, so the caller may be an application acting alone:
the operation is ``AdmissionMode.OPEN`` and ``NoCheck``. It still requires the
gateway-signed principal every public route requires, and it reads within the
caller's tenant. There is no ``user_id``: the answer is the same for every
caller, because it carries only non-user data about the Bot (see
``core/bot_overview``).

**Shape.** Engine-neutral fields plus an engine payload discriminated by
``engine``, so the published document types each engine's variant.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_overview_service import (
    BotOverviewNotFoundError,
    BotOverviewServiceProtocol,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.bot_overview.models import BotOverview
from agentclaw.community.di import Injected

#: Upper bound on the session-activity window, so one request cannot make an
#: engine aggregate unbounded history.
MAX_ACTIVITY_DAYS = 90

router = APIRouter(
    prefix="/openapi/v1/bots",
    tags=["Bots"],
    route_class=PublicAPIRoute,
    dependencies=[Depends(require_principal)],
)


@router.get("/{bot_id}/overview", response_model=Envelope[BotOverview])
@envelope_errors
async def get_bot_overview(
    request: Request,
    bot_id: BotIdPath,
    activity_days: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_ACTIVITY_DAYS,
            description="Days of session activity in sessions.daily, "
            "counting back from observed_at.",
            examples=[14],
        ),
    ] = 14,
    service: BotOverviewServiceProtocol = Injected(BotOverviewServiceProtocol),
) -> Envelope[BotOverview]:
    """Non-user overview of a Bot: engine-neutral fields plus engine payload."""
    try:
        overview = service.get_overview(bot_id, activity_days=activity_days)
    except BotOverviewNotFoundError:
        raise BotNotFoundError("Bot was not found") from None
    return envelope(overview, request)


__all__ = ["router"]
