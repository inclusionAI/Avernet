"""A bot runtime's acli market CLIs — ``/openapi/v1/bots/{bot_id}/clis``.

List what a runtime has installed, add a tool (granted, then installed), remove
one (uninstalled; the grant stays), cancel either, and read the operations
back at ``/openapi/v1/bots/{bot_id}/cli-operations``. ``stage`` picks the
runtime (service bots only); ``async`` answers at once with the operation.
Searching the market is ``GET /openapi/v1/bots/market/clis``. Switching a CLI's
identity stays at ``PATCH …/clis/{cli_code}/call-type``.

Nothing about running an operation is decided here: ``AcliCliService`` owns it.
Reads are MEMBER-level; writes are owner-only behind the edit lock, the bars of
the identity switch beside them.
"""
from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.bot_address import resolve_bot_entity
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Deleted,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import OwnerIdDep
from agentclaw.community.adapters.http.openapi_v1.errors_payloads import acli_operation_payload
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    accepted,
    deleted,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.acli_cli_service import (
    AcliAction,
    AcliCliServiceProtocol,
    AcliOperation,
)
from agentclaw.community.api.collaborator_lock_service import (
    CollaboratorLockServiceProtocol,
)
from agentclaw.community.di import Injected

from .schemas_acli_clis import (
    AcliCliOperation,
    AddBotCli,
    AddedBotCli,
    BotCliItem,
)

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/clis",
    tags=["CLIs"],
    route_class=PublicAPIRoute,
)
#: The operations themselves, under their own path.
operations_router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/cli-operations",
    tags=["CLIs"],
    route_class=PublicAPIRoute,
)

CliCodePath = Annotated[
    str, Path(description="The CLI code, as returned by the CLI listing.")
]
StageParam = Annotated[
    RuntimeStage | None,
    Query(description="The runtime: `draft` (default), `verify` or `online`. Service bots "
          "only; omit it for any other bot."),
]
AsyncParam = Annotated[
    bool,
    Query(alias="async", description="Answer at once with the operation (`202`) instead of "
          "waiting up to 30 s for it to end."),
]
_ACCEPTED = {202: {"model": Envelope[AcliCliOperation],
                   "description": "Still running; poll `…/cli-operations/{operation_id}`."}}


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


def _operation(op: AcliOperation) -> AcliCliOperation:
    return AcliCliOperation(**acli_operation_payload(op))


def _still_running(op: AcliOperation, request: Request) -> JSONResponse:
    # Still enveloped (``accepted`` builds the standard ``Envelope``). A
    # ``JSONResponse`` only because a handler's own return value always answers
    # with its route's status, 200; ``202`` has to be set on the response
    # itself, as bot creation does for its pending answer.
    return JSONResponse(
        status_code=202, content=accepted(_operation(op), request).model_dump(mode="json")
    )


def _stage(stage: RuntimeStage | None) -> str | None:
    return stage.value if stage is not None else None


@router.get("", response_model=Envelope[list[BotCliItem]])
@envelope_errors
async def list_bot_clis(
    bot_id: BotIdPath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    stage: StageParam = None,
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
) -> Envelope[list[BotCliItem]]:
    """The CLIs installed in the runtime, as its container reports them, plus the
    bot's defaults. `409` when the runtime has no running container."""
    entity_id = await resolve_bot_entity(request, bot_id=bot_id, locator=owner_id)
    items = await service.list_bot_clis(
        bot_id=bot_id, entity_id=entity_id, actor_id=user_id, stage=_stage(stage)
    )
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


@router.post("", response_model=Envelope[AddedBotCli], responses=_ACCEPTED)
@envelope_errors
async def add_bot_cli(
    bot_id: BotIdPath,
    body: AddBotCli,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    stage: StageParam = None,
    run_async: AsyncParam = False,
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
    locks: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> Envelope[AddedBotCli] | JSONResponse:
    """Grant an acli market tool, then install it in the runtime.

    `409` with the operation in progress when another add or remove is running
    in this runtime.
    """
    entity_id = await resolve_bot_entity(request, bot_id=bot_id, locator=owner_id)
    outcome = await service.add(
        bot_id=bot_id,
        entity_id=entity_id,
        actor_id=user_id,
        cli_code=body.cli_code,
        lock_epoch=await _lock_epoch(locks, bot_id=bot_id, owner_id=owner_id, user_id=user_id),
        stage=_stage(stage),
        run_async=run_async,
    )
    if outcome.operation.running or outcome.tool is None:
        return _still_running(outcome.operation, request)
    return envelope(
        AddedBotCli(
            cli_code=body.cli_code, command=outcome.tool.command, version=outcome.tool.version
        ),
        request,
    )


@router.delete("/{cli_code}", response_model=Envelope[Deleted], responses=_ACCEPTED)
@envelope_errors
async def remove_bot_cli(
    bot_id: BotIdPath,
    cli_code: CliCodePath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    stage: StageParam = None,
    run_async: AsyncParam = False,
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
    locks: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> Envelope[Deleted] | JSONResponse:
    """Uninstall the CLI from the runtime. Its grant is kept. Default CLIs cannot be removed."""
    entity_id = await resolve_bot_entity(request, bot_id=bot_id, locator=owner_id)
    outcome = await service.remove(
        bot_id=bot_id,
        entity_id=entity_id,
        actor_id=user_id,
        cli_code=cli_code,
        lock_epoch=await _lock_epoch(locks, bot_id=bot_id, owner_id=owner_id, user_id=user_id),
        stage=_stage(stage),
        run_async=run_async,
    )
    if outcome.operation.running:
        return _still_running(outcome.operation, request)
    return deleted(request)


async def _cancel(
    action: AcliAction, *, bot_id: str, cli_code: str, request: Request, user_id: str,
    owner_id: str, stage: RuntimeStage | None, run_async: bool,
    service: AcliCliServiceProtocol, locks: CollaboratorLockServiceProtocol,
) -> Envelope[AcliCliOperation] | JSONResponse:
    entity_id = await resolve_bot_entity(request, bot_id=bot_id, locator=owner_id)
    outcome = await service.cancel(
        bot_id=bot_id,
        entity_id=entity_id,
        actor_id=user_id,
        cli_code=cli_code,
        action=action,
        lock_epoch=await _lock_epoch(locks, bot_id=bot_id, owner_id=owner_id, user_id=user_id),
        stage=_stage(stage),
        run_async=run_async,
    )
    if outcome.operation.running:
        return _still_running(outcome.operation, request)
    return envelope(_operation(outcome.operation), request)


@router.post(
    "/{cli_code}/install/cancel", response_model=Envelope[AcliCliOperation], responses=_ACCEPTED
)
@envelope_errors
async def cancel_bot_cli_install(
    bot_id: BotIdPath,
    cli_code: CliCodePath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    stage: StageParam = None,
    run_async: AsyncParam = False,
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
    locks: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> Envelope[AcliCliOperation] | JSONResponse:
    """Cancel the running add of the CLI; whatever it installed is removed. The grant stays."""
    return await _cancel(
        AcliAction.INSTALL, bot_id=bot_id, cli_code=cli_code, request=request, user_id=user_id,
        owner_id=owner_id, stage=stage, run_async=run_async, service=service, locks=locks,
    )


@router.post(
    "/{cli_code}/uninstall/cancel", response_model=Envelope[AcliCliOperation], responses=_ACCEPTED
)
@envelope_errors
async def cancel_bot_cli_uninstall(
    bot_id: BotIdPath,
    cli_code: CliCodePath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    stage: StageParam = None,
    run_async: AsyncParam = False,
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
    locks: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> Envelope[AcliCliOperation] | JSONResponse:
    """Cancel the running remove of the CLI; it is reinstalled if it was already removed."""
    return await _cancel(
        AcliAction.UNINSTALL, bot_id=bot_id, cli_code=cli_code, request=request, user_id=user_id,
        owner_id=owner_id, stage=stage, run_async=run_async, service=service, locks=locks,
    )


@operations_router.get("", response_model=Envelope[AcliCliOperation | None])
@envelope_errors
async def get_live_cli_operation(
    bot_id: BotIdPath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    stage: StageParam = None,
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
) -> Envelope[AcliCliOperation | None]:
    """The runtime's running add or remove (there is at most one), or `null`.

    A cancel in progress shows as the operation's `cancelling` step; the cancel
    itself reads back at `…/cli-operations/{operation_id}`.
    """
    entity_id = await resolve_bot_entity(request, bot_id=bot_id, locator=owner_id)
    op = await service.live_operation(
        bot_id=bot_id, entity_id=entity_id, actor_id=user_id, stage=_stage(stage)
    )
    return envelope(_operation(op) if op is not None else None, request)


@operations_router.get("/{operation_id}", response_model=Envelope[AcliCliOperation])
@envelope_errors
async def get_cli_operation(
    bot_id: BotIdPath,
    operation_id: Annotated[
        str, Path(min_length=1, max_length=64,
                  description="The operation id an add, remove or cancel answered with.")
    ],
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
) -> Envelope[AcliCliOperation]:
    """One add, remove or cancel of this bot, running or ended."""
    entity_id = await resolve_bot_entity(request, bot_id=bot_id, locator=owner_id)
    op = await service.get_operation(
        bot_id=bot_id, entity_id=entity_id, actor_id=user_id, operation_id=operation_id
    )
    return envelope(_operation(op), request)


__all__ = ["operations_router", "router"]
