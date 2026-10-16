"""Search the acli CLI market — ``GET /openapi/v1/bots/market/clis``.

The market's catalogue is identical for every caller, so the search is not
tied to a bot: it calls the market's API from the backend. Add a result to a
bot with ``POST /openapi/v1/bots/{bot_id}/clis`` using its ``name``.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.bots.schemas_acli_clis import (
    AcliMarketItem,
    AcliMarketPageResult,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.acli_cli_service import AcliCliServiceProtocol
from agentclaw.community.di import Injected

router = APIRouter(
    prefix="/openapi/v1/bots/market",
    tags=["market"],
    route_class=PublicAPIRoute,
)


@router.get(
    "/clis",
    response_model=Envelope[AcliMarketPageResult],
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def search_acli_market(
    request: Request,
    keyword: Annotated[
        str, Query(max_length=100, description="Search text; empty lists the whole market.")
    ] = "",
    page_no: Annotated[int, Query(ge=1, le=10_000, description="1-based page.")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Page size.")] = 20,
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
) -> Envelope[AcliMarketPageResult]:
    """One page of published acli market tools matching ``keyword``."""
    page = await service.search_market(keyword=keyword, page_no=page_no, page_size=page_size)
    return envelope(
        AcliMarketPageResult(
            items=[
                AcliMarketItem(
                    name=t.name, command=t.command, description=t.description, version=t.version
                )
                for t in page.items
            ],
            total=page.total,
            has_more=page.has_more,
        ),
        request,
    )


__all__ = ["router"]
