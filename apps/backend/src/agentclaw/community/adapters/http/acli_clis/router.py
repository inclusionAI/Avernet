"""Internal ``/api/bots`` routes for a bot's acli market CLIs.

The same operations as the public ``/openapi/v1/bots/{bot_id}/clis`` API, for
callers authenticated by ``get_current_user``. The actor is always the
authenticated user; ``entity_id`` only locates the bot. The service enforces
membership for the list, and ownership, the active-bot rule and the edit lock
for add and remove; its domain errors reach
the client through the app-level status map (``acli_cli_status.py``).
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from agentclaw.community.adapters.http.acli_clis.schemas import (
    AcliMarketItem,
    AcliMarketPageResponse,
    AddBotCliRequest,
    AddedBotCli,
    BotCliItem,
    RemovedBotCli,
)
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.api.acli_cli_service import AcliCliServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/bots", tags=["acli-clis"])

EntityId = Annotated[str, Query(min_length=1, description="The bot's entity (its owner).")]


# Declared before the ``{bot_id}`` routes so ``market`` is never read as a bot id.
@router.get("/market/clis", response_model=AcliMarketPageResponse)
async def search_acli_market(
    keyword: Annotated[str, Query(max_length=100)] = "",
    page_no: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
) -> AcliMarketPageResponse:
    """One page of published acli market tools; not tied to a bot."""
    page = await service.search_market(keyword=keyword, page_no=page_no, page_size=page_size)
    return AcliMarketPageResponse(
        items=[
            AcliMarketItem(
                name=t.name, command=t.command, description=t.description, version=t.version
            )
            for t in page.items
        ],
        total=page.total,
        has_more=page.has_more,
    )


@router.get("/{bot_id}/clis", response_model=list[BotCliItem])
async def list_bot_clis(
    bot_id: str,
    entity_id: EntityId,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
) -> list[BotCliItem]:
    """Every CLI the bot is granted, with its identity mode and default flag."""
    # COSEC: the service refuses an actor who is not at least a member of the bot.
    items = await asyncio.to_thread(
        service.list_bot_clis, bot_id=bot_id, entity_id=entity_id, actor_id=user.staffId
    )
    return [
        BotCliItem(
            cli_code=item.cli_code,
            cli_name=item.cli_name,
            cli_desc=item.cli_desc,
            identity_mode=item.identity_mode,
            default=item.default,
        )
        for item in items
    ]


@router.post("/{bot_id}/clis", response_model=AddedBotCli)
async def add_bot_cli(
    bot_id: str,
    request: AddBotCliRequest,
    entity_id: EntityId,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
) -> AddedBotCli:
    """Install an acli market tool in the running bot, then grant it."""
    logger.info(
        "acli_cli_http_add_started bot_id=%s cli_code=%s lock_epoch_supplied=%s",
        bot_id, request.cli_code, request.lock_epoch is not None,
    )
    # COSEC: the actor comes only from the authenticated request context.
    tool = await service.add(
        bot_id=bot_id,
        entity_id=entity_id,
        actor_id=user.staffId,
        cli_code=request.cli_code,
        lock_epoch=request.lock_epoch,
    )
    return AddedBotCli(cli_code=request.cli_code, command=tool.command, version=tool.version)


@router.delete("/{bot_id}/clis/{cli_code}", response_model=RemovedBotCli)
async def remove_bot_cli(
    bot_id: str,
    cli_code: str,
    entity_id: EntityId,
    lock_epoch: Annotated[int | None, Query(gt=0)] = None,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
) -> RemovedBotCli:
    """Revoke a CLI, then uninstall it. Default CLIs are refused."""
    logger.info(
        "acli_cli_http_remove_started bot_id=%s cli_code=%s lock_epoch_supplied=%s",
        bot_id, cli_code, lock_epoch is not None,
    )
    # COSEC: the actor comes only from the authenticated request context.
    await service.remove(
        bot_id=bot_id,
        entity_id=entity_id,
        actor_id=user.staffId,
        cli_code=cli_code,
        lock_epoch=lock_epoch,
    )
    return RemovedBotCli(cli_code=cli_code, deleted=True)


__all__ = ["router"]
