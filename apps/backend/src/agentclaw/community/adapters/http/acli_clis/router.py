"""Internal ``/api/bots`` routes for a bot runtime's acli market CLIs.

The same operations as the public ``/openapi/v1/bots/{bot_id}/clis`` API, for
callers authenticated by ``get_current_user``. The actor is always the
authenticated user; ``entity_id`` only locates the bot. The service enforces
membership for reads, and ownership, the active-bot rule and the edit lock
for writes; its domain errors reach the client through the app-level status
map (``acli_cli_status.py``). A request still running after its wait is
``202`` with the operation; one refused because another is running is ``409``
with that one.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse

from agentclaw.community.adapters.http.acli_clis.schemas import (
    AcliMarketItem,
    AcliMarketPageResponse,
    AddBotCliRequest,
    AddedBotCli,
    BotCliItem,
    CliOperation,
    RemovedBotCli,
)
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.api.acli_cli_service import (
    AcliAction,
    AcliCliServiceProtocol,
    AcliOperation,
    AcliOperationInProgressError,
    AcliOutcome,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/bots", tags=["acli-clis"])

EntityId = Annotated[str, Query(min_length=1, description="The bot's entity (its owner).")]
Stage = Annotated[
    Literal["draft", "verify", "online"] | None,
    Query(description="The runtime; service bots only, default draft."),
]
RunAsync = Annotated[bool, Query(alias="async", description="Answer at once with the operation.")]


def _operation(op: AcliOperation) -> CliOperation:
    return CliOperation(
        operation_id=op.id, kind=op.kind, action=op.action, cli_code=op.cli_code,
        stage=op.stage.value, status=op.status, step=op.step,
        error_code=op.error_code, error_message=op.error_message,
    )


def _still_running(op: AcliOperation) -> JSONResponse:
    return JSONResponse(status_code=202, content=_operation(op).model_dump(mode="json"))


async def _write(call) -> AcliOutcome | JSONResponse:
    """Run a write, answering a refusal-for-a-running-operation with that operation."""
    try:
        return await call
    except AcliOperationInProgressError as error:
        op = error.operation
        return JSONResponse(
            status_code=409,
            content={
                "detail": error.detail,
                "operation": (
                    _operation(op).model_dump(mode="json")
                    if isinstance(op, AcliOperation) else None
                ),
            },
        )


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
    stage: Stage = None,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
) -> list[BotCliItem]:
    """The CLIs installed in the runtime, plus the bot's defaults."""
    # COSEC: the service refuses an actor who is not at least a member of the bot.
    items = await service.list_bot_clis(
        bot_id=bot_id, entity_id=entity_id, actor_id=user.staffId, stage=stage
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
    stage: Stage = None,
    run_async: RunAsync = False,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
) -> AddedBotCli | JSONResponse:
    """Grant an acli market tool, then install it in the runtime."""
    logger.info(
        "acli_cli_http_add_started bot_id=%s cli_code=%s lock_epoch_supplied=%s",
        bot_id, request.cli_code, request.lock_epoch is not None,
    )
    # COSEC: the actor comes only from the authenticated request context.
    outcome = await _write(service.add(
        bot_id=bot_id,
        entity_id=entity_id,
        actor_id=user.staffId,
        cli_code=request.cli_code,
        lock_epoch=request.lock_epoch,
        stage=stage,
        run_async=run_async,
    ))
    if isinstance(outcome, JSONResponse):
        return outcome
    if outcome.operation.running or outcome.tool is None:
        return _still_running(outcome.operation)
    return AddedBotCli(
        cli_code=request.cli_code, command=outcome.tool.command, version=outcome.tool.version
    )


@router.delete("/{bot_id}/clis/{cli_code}", response_model=RemovedBotCli)
async def remove_bot_cli(
    bot_id: str,
    cli_code: str,
    entity_id: EntityId,
    lock_epoch: Annotated[int | None, Query(gt=0)] = None,
    stage: Stage = None,
    run_async: RunAsync = False,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
) -> RemovedBotCli | JSONResponse:
    """Uninstall a CLI from the runtime; its grant is kept. Default CLIs are refused."""
    logger.info(
        "acli_cli_http_remove_started bot_id=%s cli_code=%s lock_epoch_supplied=%s",
        bot_id, cli_code, lock_epoch is not None,
    )
    # COSEC: the actor comes only from the authenticated request context.
    outcome = await _write(service.remove(
        bot_id=bot_id,
        entity_id=entity_id,
        actor_id=user.staffId,
        cli_code=cli_code,
        lock_epoch=lock_epoch,
        stage=stage,
        run_async=run_async,
    ))
    if isinstance(outcome, JSONResponse):
        return outcome
    if outcome.operation.running:
        return _still_running(outcome.operation)
    return RemovedBotCli(cli_code=cli_code, deleted=True)


@router.post("/{bot_id}/clis/{cli_code}/{action}/cancel", response_model=CliOperation)
async def cancel_bot_cli_operation(
    bot_id: str,
    cli_code: str,
    action: Annotated[AcliAction, Path(description="`install` (an add) or `uninstall` (a remove).")],
    entity_id: EntityId,
    lock_epoch: Annotated[int | None, Query(gt=0)] = None,
    stage: Stage = None,
    run_async: RunAsync = False,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
) -> CliOperation | JSONResponse:
    """Cancel the running add (``install``) or remove (``uninstall``) of a CLI."""
    # COSEC: the actor comes only from the authenticated request context.
    outcome = await _write(service.cancel(
        bot_id=bot_id,
        entity_id=entity_id,
        actor_id=user.staffId,
        cli_code=cli_code,
        action=action,
        lock_epoch=lock_epoch,
        stage=stage,
        run_async=run_async,
    ))
    if isinstance(outcome, JSONResponse):
        return outcome
    if outcome.operation.running:
        return _still_running(outcome.operation)
    return _operation(outcome.operation)


@router.get("/{bot_id}/cli-operations", response_model=CliOperation | None)
async def get_live_cli_operation(
    bot_id: str,
    entity_id: EntityId,
    stage: Stage = None,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
) -> CliOperation | None:
    """The runtime's running add or remove (at most one), or ``null``.

    A cancel in progress shows as the operation's ``cancelling`` step.
    """
    op = await service.live_operation(
        bot_id=bot_id, entity_id=entity_id, actor_id=user.staffId, stage=stage
    )
    return _operation(op) if op is not None else None


@router.get("/{bot_id}/cli-operations/{operation_id}", response_model=CliOperation)
async def get_cli_operation(
    bot_id: str,
    operation_id: str,
    entity_id: EntityId,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AcliCliServiceProtocol = Injected(AcliCliServiceProtocol),
) -> CliOperation:
    """One add, remove or cancel of this bot, running or ended."""
    op = await service.get_operation(
        bot_id=bot_id, entity_id=entity_id, actor_id=user.staffId, operation_id=operation_id
    )
    return _operation(op)


__all__ = ["router"]
