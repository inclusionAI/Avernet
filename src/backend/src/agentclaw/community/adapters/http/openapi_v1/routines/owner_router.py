"""Owner-level routine listing — ``GET /openapi/v1/bots/routines/all``.

The per-bot group lists one bot's draft workspace; this route aggregates the
named user's whole fleet — bots owned or collaborated on — across every
runtime stage, the way the legacy ``/api/cron`` listing always did. The
service layer owns the fan-out, the bot_name decoration, the per-stage dedup
and the partial-failure tolerance; this is the public-face adapter over it.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    DelegatedUserIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import (
    PublicAPIRoute,
)
from agentclaw.community.api.cron_relay_service import (
    CronRelayServiceProtocol,
)
from agentclaw.community.di import Injected

from .router import _map_routine
from .schemas import Routine

router = APIRouter(
    prefix="/openapi/v1/bots/routines/all",
    tags=["routines"],
    route_class=PublicAPIRoute,
)


@router.get("", response_model=Envelope[Page[Routine]])
@envelope_errors
async def list_owner_routines(
    page: PageParamsDep,
    owner_id: DelegatedUserIdDep,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[Page[Routine]]:
    """List the named user's routines across all their bots (paginated).

    Every runtime stage is aggregated — draft, verify and online — so a
    service bot's published runtimes appear alongside its draft workspace,
    and one definition can answer more than one row, differing by
    `runtime_stage`. Bots the user collaborates on are included, matching
    the listing the internal console has always shown.

    For an application caller, reading it requires a live user-level
    authorization from the named user; without one the user is answered as
    if they did not exist.
    """
    # Names no bot, so there is no bot grant to check against — but the
    # answer is still about a person's fleet, and a stranger application must
    # not read it by naming a user id. Gated like the ceiling, by
    # ``DelegatedUserIdDep``: the application must hold the user's
    # account-level grant.
    result = await factory.list_all_crons(
        user_id=owner_id,
        nick_name=owner_id,
        bot_id=None,
        runtime_stage=None,
    )
    data = result.get("data") if isinstance(result, dict) else None
    if isinstance(data, list):
        items_list = data
    elif isinstance(data, dict):
        items_list = data.get("items", [])
    else:
        items_list = []
    mapped = [_map_routine(d) for d in items_list if isinstance(d, dict)]
    start = (page.page - 1) * page.page_size
    end = start + page.page_size
    page_items = mapped[start:end]
    return page_envelope(len(mapped), page_items, request)
