"""List only Bots on which the acting user is a collaborator."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.clusters import cluster_for_engine
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    UserIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.responses import envelope_errors, page
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.models import CollaboratorRecord
from agentclaw.community.di import Injected

from .schemas import CollaboratingBot, CollaborationSummary

router = APIRouter(
    prefix="/openapi/v1/bots/collaborations",
    tags=["bots"],
    route_class=PublicAPIRoute,
)


def _to_item(bot: dict[str, Any], relation: CollaboratorRecord) -> CollaboratingBot:
    engine = bot.get("active_engine") or ""
    return CollaboratingBot(
        bot_id=bot["bot_id"],
        bot_name=bot.get("bot_name") or "",
        bot_desc=bot.get("bot_desc") or "",
        entity_id=bot.get("entity_id") or "",
        owner_id=bot.get("owner_id") or "",
        engine=engine,
        cluster_name=cluster_for_engine(engine),
        bot_type=bot.get("bot_type") or "",
        status=bot.get("status") or "",
        collaboration=CollaborationSummary(
            id=relation.id,
            role=relation.role,
            joined_at=relation.gmt_create,
        ),
    )


@router.get("", response_model=Envelope[Page[CollaboratingBot]])
@envelope_errors
async def list_collaborating_bots(
    request: Request,
    page_params: PageParamsDep,
    user_id: UserIdDep,
    caller: ActingCallerDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    collaborators: CollaboratorServiceProtocol = Injected(
        CollaboratorServiceProtocol
    ),
) -> Envelope[Page[CollaboratingBot]]:
    """List live Bots for which the acting user has a collaborator record.

    Owners are deliberately absent: this collection represents collaboration,
    not every Bot the user can operate. Application callers see only exact
    Bot/owner pairs delegated to that application.
    """
    relations = await asyncio.to_thread(
        collaborators.list_user_collaborations, user_id
    )
    granted_pairs = caller.granted_bot_owner_pairs()
    if granted_pairs is not None:
        relations = [
            relation
            for relation in relations
            if (relation.bot_id, relation.owner_id) in granted_pairs
        ]
    if not relations:
        return page(0, [], request)

    relation_by_pair = {
        (relation.bot_id, relation.owner_id): relation for relation in relations
    }
    result = await asyncio.to_thread(
        bot_service.list_bots_by_owner_bot_pairs,
        pairs=list(relation_by_pair),
        page=page_params.page,
        page_size=page_params.page_size,
    )
    items = [
        _to_item(bot, relation_by_pair[(bot["bot_id"], bot["owner_id"])])
        for bot in result["items"]
    ]
    return page(result["total"], items, request)


__all__ = ["router"]
