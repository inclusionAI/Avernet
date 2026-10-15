"""A bot's acli market CLIs — ``/openapi/v1/bots/{bot_id}/clis``.

List what the bot is granted, search the acli market, add a tool (installed in
the running container, then granted) and remove one (revoked, then
uninstalled). Switching a CLI's identity stays at
``PATCH …/clis/{cli_code}/call-type``.

Nothing about ordering or rollback is decided here: ``AcliCliService`` owns
both. Reads are MEMBER-level; writes are owner-only behind the edit lock, the
bars of the identity switch beside them.
"""
from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Deleted,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import OwnerIdDep
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    deleted,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.acli_cli_service import AcliCliServiceProtocol
from agentclaw.community.api.collaborator_lock_service import (
    CollaboratorLockServiceProtocol,
)
from agentclaw.community.di import Injected

from .schemas_acli_clis import (
    AcliMarketItem,
    AcliMarketPageResult,
    AddBotCli,
    AddedBotCli,
    BotCliItem,
)

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/clis",
    tags=["CLIs"],
    route_class=PublicAPIRoute,
)

CliCodePath = Annotated[
    str, Path(description="The CLI code, as returned by the CLI listing.")
]


async def _lock_epoch(
    locks: CollaboratorLockServiceProtocol, *, bot_id: str, owner_id: str, user_id: str
) -> int | None:
    """Carry the lock validated by ``Check(..., EDIT_LOCK)`` into the write."""
    info = await asyncio.to_thread(
        locks.get_lock_info, bot_id=bot_id, owner_id=owner_id, user_id=user_id
    )
    lock = info.lock
    if lock is None or lock.holder_user_id != user_id:
        return None
    epoch = lock.id
    return epoch if isinstance(epoch, int) and not isinstance(epoch, bool) else None


@router.get("", response_model=Envelope[list[BotCliItem]])
@envelope_errors
async def list_bot_clis(
    bot_id: BotIdPath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
) -> Envelope[list[BotCliItem]]:
    """Every CLI the bot is granted, whether or not it was added here."""
    items = await asyncio.to_thread(service.list_bot_clis, bot_id=bot_id, entity_id=owner_id)
    return envelope(
        [
            BotCliItem(
                cli_code=item.cli_code,
                cli_name=item.cli_name,
                cli_desc=item.cli_desc,
                identity_mode=item.identity_mode,
                default=item.default,
            )
            for item in items
        ],
        request,
    )


@router.get("/market", response_model=Envelope[AcliMarketPageResult])
@envelope_errors
async def search_acli_market(
    bot_id: BotIdPath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    keyword: Annotated[
        str, Query(max_length=100, description="Search text; empty lists the whole market.")
    ] = "",
    page_no: Annotated[int, Query(ge=1, le=10_000, description="1-based page.")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Page size.")] = 20,
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
) -> Envelope[AcliMarketPageResult]:
    """Search the acli market from inside the bot's running container."""
    page = await service.search_market(
        bot_id=bot_id, entity_id=owner_id, keyword=keyword, page_no=page_no, page_size=page_size
    )
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


@router.post("", response_model=Envelope[AddedBotCli])
@envelope_errors
async def add_bot_cli(
    bot_id: BotIdPath,
    body: AddBotCli,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
    locks: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> Envelope[AddedBotCli]:
    """Install an acli market tool in the running bot, then grant it as `owner`."""
    tool = await service.add(
        bot_id=bot_id,
        entity_id=owner_id,
        actor_id=user_id,
        cli_code=body.cli_code,
        lock_epoch=await _lock_epoch(locks, bot_id=bot_id, owner_id=owner_id, user_id=user_id),
    )
    return envelope(
        AddedBotCli(cli_code=body.cli_code, command=tool.command, version=tool.version),
        request,
    )


@router.delete("/{cli_code}", response_model=Envelope[Deleted])
@envelope_errors
async def remove_bot_cli(
    bot_id: BotIdPath,
    cli_code: CliCodePath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
    locks: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> Envelope[Deleted]:
    """Revoke the CLI, then uninstall it. Default CLIs cannot be removed."""
    await service.remove(
        bot_id=bot_id,
        entity_id=owner_id,
        actor_id=user_id,
        cli_code=cli_code,
        lock_epoch=await _lock_epoch(locks, bot_id=bot_id, owner_id=owner_id, user_id=user_id),
    )
    return deleted(request)


__all__ = ["router"]
