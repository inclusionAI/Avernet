"""Minimal, authenticated Bot classification for group participant routing."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    refuse_app_only_caller,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.di import Injected


class BotClassification(BaseModel):
    """Allowlisted fields available before an owner/editor relationship exists."""

    bot_id: str = Field(description="The external Bot identifier.", examples=["bot-1"])
    bot_type: Literal["personal", "service", "desktop"] = Field(
        description=(
            "Bot type: personal for individual use, service for published stage routing, "
            "or desktop for a desktop-managed Bot."
        ),
        examples=["service"],
    )


router = APIRouter(
    prefix="/openapi/v1/bots",
    tags=["Bots"],
    route_class=PublicAPIRoute,
    dependencies=[Depends(refuse_app_only_caller)],
)


@router.get("/{bot_id}/classification", response_model=Envelope[BotClassification])
@envelope_errors
async def get_bot_classification(
    request: Request,
    bot_id: BotIdPath,
    entity_id: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=1024,
            description="Entity suffix of a composite Bot identity; omit for a unique Bot ID.",
            examples=["owner-1"],
        ),
    ] = None,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
):
    """Read only Bot ID/type, without requiring owner or collaborator access.

    Composite BCS actor IDs are split by the caller: Bot ID in the path and
    entity suffix in the optional query. Omission uses the unique Bot lookup.
    A signed, authenticated user is required; only tenant-local records are read.
    """
    result = bot_service.get_bot_classification(bot_id, entity_id=entity_id)
    if result is None:
        raise BotNotFoundError("Bot classification was not found")
    return envelope(BotClassification.model_validate(result), request)
