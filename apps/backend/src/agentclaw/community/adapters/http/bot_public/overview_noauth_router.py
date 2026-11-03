"""Unauthenticated Bot overview.

- GET /api/public/bots/{bot_id}/overview

No authentication on purpose: the response carries only non-user data about
the Bot (see ``core/bot_overview``). The engine payload is a discriminated
union, so the generated OpenAPI document types each engine's variant.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query

from agentclaw.community.api.bot_overview_service import (
    BotOverviewNotFoundError,
    BotOverviewServiceProtocol,
)
from agentclaw.community.core.bot_overview.models import BotOverview
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/public/bots", tags=["bot-overview-noauth"])

#: Upper bound on the session-activity window a caller may ask for, so one
#: unauthenticated request cannot make an engine aggregate unbounded history.
MAX_ACTIVITY_DAYS = 90


@router.get(
    "/{bot_id}/overview",
    response_model=BotOverview,
    responses={404: {"description": "No Bot has this id."}},
)
def get_bot_overview(
    bot_id: str = Path(..., description="Bot ID"),
    activity_days: int = Query(
        14,
        ge=1,
        le=MAX_ACTIVITY_DAYS,
        description="Days of session activity to aggregate in "
        "``sessions.daily``, counting back from ``observed_at``.",
    ),
    service: BotOverviewServiceProtocol = Injected(BotOverviewServiceProtocol),
) -> BotOverview:
    """Non-user overview of a Bot: engine-neutral fields plus engine payload."""
    try:
        overview = service.get_overview(bot_id, activity_days=activity_days)
    except BotOverviewNotFoundError:
        raise HTTPException(status_code=404, detail="Bot not found") from None
    logger.info(
        "[public_noauth.get_bot_overview] bot_id=%s engine=%s", bot_id, overview.engine
    )
    return overview
