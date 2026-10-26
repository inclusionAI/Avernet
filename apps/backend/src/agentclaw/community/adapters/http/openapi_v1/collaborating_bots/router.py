"""List only Bots on which the acting user is a collaborator."""

from __future__ import annotations

import asyncio
from typing import Any

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
from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol
from agentclaw.community.core.bot_collaborator.models import CollaboratorRecord
from agentclaw.community.core.bot_inventory.errors import BotInventoryPermissionError
from agentclaw.community.core.bot_inventory.protocols import (
    BotInventoryTemplatePort,
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.di import Injected
from fastapi import APIRouter, Request

from .schemas import CollaboratingBot, CollaborationSummary

router = APIRouter(
    prefix="/openapi/v1/bots/collaborations",
    tags=["bots"],
    route_class=PublicAPIRoute,
)


def _to_item(
    bot: dict[str, Any],
    relation: CollaboratorRecord,
    *,
    template_config: dict[str, Any] | None,
    space: dict[str, str] | None,
) -> CollaboratingBot:
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
        template_type=bot.get("template_type") or None,
        template_config=template_config,
        space=space,
        collaboration=CollaborationSummary(
            id=relation.id,
            role=relation.role,
            joined_at=relation.gmt_create,
        ),
    )


def _resolve_template_configs(
    rows: list[dict[str, Any]], template_port: BotInventoryTemplatePort
) -> dict[str, dict[str, Any]]:
    bot_ids = [
        str(row["bot_id"])
        for row in rows
        if row.get("bot_id") and row.get("template_type")
    ]
    return template_port.list_template_configs_by_bot_ids(list(dict.fromkeys(bot_ids)))


def _resolve_row_spaces(
    rows: list[dict[str, Any]], space_context: BusinessSpaceContextProtocol
) -> dict[tuple[str, str], dict[str, str] | None]:
    resolved: dict[tuple[str, str], dict[str, str] | None] = {}
    for row in rows:
        owner_id = str(row.get("owner_id") or "")
        space_id = str(row.get("space_id") or "")
        key = (owner_id, space_id)
        if key in resolved:
            continue
        try:
            ref = space_context.bot_space(
                bot=row, owner_id=owner_id, current_space=None
            )
        except BotInventoryPermissionError:
            ref = None
        resolved[key] = (
            {"space_id": ref.space_id, "name": ref.name, "kind": ref.kind}
            if ref is not None
            else None
        )
    return resolved


@router.get("", response_model=Envelope[Page[CollaboratingBot]])
@envelope_errors
async def list_collaborating_bots(
    request: Request,
    page_params: PageParamsDep,
    user_id: UserIdDep,
    caller: ActingCallerDep,
    bot_repo: BotRepository = Injected(BotRepository),
    collaborators: CollaboratorServiceProtocol = Injected(
        CollaboratorServiceProtocol
    ),
    template_port: BotInventoryTemplatePort = Injected(BotInventoryTemplatePort),
    space_context: BusinessSpaceContextProtocol = Injected(
        BusinessSpaceContextProtocol
    ),
) -> Envelope[Page[CollaboratingBot]]:
    """List live Bots for which the acting user has a collaborator record.

    Owners are deliberately absent: this collection represents collaboration,
    not every Bot the user can operate. Application callers see only exact
    Bot/owner pairs delegated to that application.
    """
    granted_pairs = caller.granted_bot_owner_pairs()
    if granted_pairs is not None and not granted_pairs:
        return page(0, [], request)

    relations = await asyncio.to_thread(
        collaborators.list_user_collaborations, user_id
    )
    if not relations:
        return page(0, [], request)

    relation_by_pk = {relation.bot_pk: relation for relation in relations}
    total, rows = await asyncio.to_thread(
        bot_repo.page_live_bots_by_pks,
        bot_pks=list(relation_by_pk),
        exclude_owner_id=user_id,
        entity_pairs=list(granted_pairs) if granted_pairs is not None else None,
        page=page_params.page,
        page_size=page_params.page_size,
    )
    template_configs, row_spaces = await asyncio.gather(
        asyncio.to_thread(_resolve_template_configs, rows, template_port),
        asyncio.to_thread(_resolve_row_spaces, rows, space_context),
    )
    items = []
    for bot in rows:
        owner_id = str(bot.get("owner_id") or "")
        bot_id = str(bot.get("bot_id") or "")
        items.append(
            _to_item(
                bot,
                relation_by_pk[bot["id"]],
                template_config=template_configs.get(bot_id),
                space=row_spaces.get((owner_id, str(bot.get("space_id") or ""))),
            )
        )
    return page(total, items, request)


__all__ = ["router"]
