"""Internal acli CLI routes: the actor is always the authenticated user."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.adapters.http.acli_clis.router import (
    add_bot_cli,
    cancel_bot_cli_operation,
    get_cli_operation,
    list_bot_clis,
    get_live_cli_operation,
    remove_bot_cli,
    router,
)
from agentclaw.community.adapters.http.acli_clis.schemas import AddBotCliRequest
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.api.acli_cli_service import (
    AcliAction,
    AcliOperation,
    AcliOperationInProgressError,
    AcliOutcome,
    AcliTool,
    BotCli,
    OperationKind,
    OperationStatus,
)
from agentclaw.community.core.service_bot.types import PublishStage

pytestmark = pytest.mark.asyncio

_USER = AuthenticatedUser(id="owner-1", staffId="owner-1", operatorName="owner-1")


def _op(status: str = "succeeded", **fields) -> AcliOperation:
    values = {"kind": "operation", "action": "install", **fields}
    return AcliOperation(
        id="a" * 32, kind=OperationKind(values["kind"]), action=AcliAction(values["action"]),
        cli_code="deepinsight-cli", stage=PublishStage.DRAFT,
        status=OperationStatus(status), step=None,
    )


async def test_add_passes_the_authenticated_actor_and_the_entity() -> None:
    service = MagicMock()
    service.add = AsyncMock(return_value=AcliOutcome(
        operation=_op(), tool=AcliTool("deepinsight-cli", "di", "DI", "1.0.9")
    ))

    added = await add_bot_cli(
        "bot-1", AddBotCliRequest(cli_code="deepinsight-cli", lock_epoch=7), "entity-1",
        "verify", False, _USER, service,
    )

    service.add.assert_awaited_once_with(
        bot_id="bot-1", entity_id="entity-1", actor_id="owner-1",
        cli_code="deepinsight-cli", lock_epoch=7, stage="verify", run_async=False,
    )
    assert (added.cli_code, added.command, added.version) == ("deepinsight-cli", "di", "1.0.9")


async def test_a_running_add_is_202_and_a_conflict_is_409_with_the_live_one() -> None:
    service = MagicMock()
    service.add = AsyncMock(return_value=AcliOutcome(operation=_op("running")))

    running = await add_bot_cli(
        "bot-1", AddBotCliRequest(cli_code="x"), "entity-1", None, True, _USER, service
    )
    assert running.status_code == 202
    assert json.loads(running.body)["status"] == "running"

    service.add = AsyncMock(side_effect=AcliOperationInProgressError(_op("running")))
    refused = await add_bot_cli(
        "bot-1", AddBotCliRequest(cli_code="x"), "entity-1", None, False, _USER, service
    )
    assert refused.status_code == 409
    assert json.loads(refused.body)["operation"]["operation_id"] == "a" * 32


async def test_remove_passes_the_authenticated_actor_and_the_lock_epoch() -> None:
    service = MagicMock()
    service.remove = AsyncMock(return_value=AcliOutcome(operation=_op(action="uninstall")))

    removed = await remove_bot_cli(
        "bot-1", "yuque-cli", "entity-1", 3, None, False, _USER, service
    )

    service.remove.assert_awaited_once_with(
        bot_id="bot-1", entity_id="entity-1", actor_id="owner-1",
        cli_code="yuque-cli", lock_epoch=3, stage=None, run_async=False,
    )
    assert (removed.cli_code, removed.deleted) == ("yuque-cli", True)
    service.remove = AsyncMock(return_value=AcliOutcome(operation=_op("running")))
    assert (await remove_bot_cli(
        "bot-1", "yuque-cli", "entity-1", 3, None, True, _USER, service
    )).status_code == 202


async def test_cancel_passes_the_action() -> None:
    service = MagicMock()
    service.cancel = AsyncMock(return_value=AcliOutcome(operation=_op(kind="cancel")))

    op = await cancel_bot_cli_operation(
        "bot-1", "yuque-cli", AcliAction.UNINSTALL, "entity-1", 3, None, False, _USER, service
    )

    assert op.kind == "cancel"
    assert service.cancel.await_args.kwargs["action"] == "uninstall"
    service.cancel = AsyncMock(return_value=AcliOutcome(operation=_op("running")))
    assert (await cancel_bot_cli_operation(
        "bot-1", "yuque-cli", AcliAction.INSTALL, "entity-1", 3, None, True, _USER, service
    )).status_code == 202


async def test_list_and_operations() -> None:
    service = MagicMock()
    service.list_bot_clis = AsyncMock(return_value=[BotCli("dataphin", "Dataphin", "", "owner", True)])
    service.live_operation = AsyncMock(return_value=_op("running"))
    service.get_operation = AsyncMock(return_value=_op())

    items = await list_bot_clis("bot-1", "entity-1", None, _USER, service)
    live = await get_live_cli_operation("bot-1", "entity-1", "online", _USER, service)
    one = await get_cli_operation("bot-1", "a" * 32, "entity-1", _USER, service)

    service.list_bot_clis.assert_awaited_once_with(
        bot_id="bot-1", entity_id="entity-1", actor_id="owner-1", stage=None
    )
    assert [(i.cli_code, i.identity_mode, i.default) for i in items] == [("dataphin", "owner", True)]
    assert live.status == "running"
    assert one.operation_id == "a" * 32


def test_market_route_is_declared_before_the_bot_id_routes() -> None:
    paths = [route.path for route in router.routes]
    assert paths.index("/api/bots/market/clis") < paths.index("/api/bots/{bot_id}/clis")


def test_add_body_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        AddBotCliRequest(cli_code="x", user_id="someone-else")  # type: ignore[call-arg]
