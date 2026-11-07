"""``/openapi/v1/bots/{bot_id}/clis`` — handlers, wire shapes and error mapping."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.authorization_rules import (
    AUTHORIZATION,
    EDIT_LOCK,
)
from agentclaw.community.adapters.http.openapi_v1.bots.acli_clis import (
    add_bot_cli,
    cancel_bot_cli_install,
    cancel_bot_cli_uninstall,
    get_cli_operation,
    list_bot_clis,
    get_live_cli_operation,
    operations_router,
    remove_bot_cli,
    router,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from agentclaw.community.adapters.http.openapi_v1.market.acli_clis import (
    router as market_router,
    search_acli_market,
)
from agentclaw.community.adapters.http.openapi_v1.bots.schemas_acli_clis import AddBotCli
from agentclaw.community.api.acli_cli_service import (
    AcliAction,
    AcliBotNotRunningError,
    AcliCliNameMismatchError,
    AcliDefaultCliError,
    AcliEngineOutdatedError,
    AcliEngineUnsupportedError,
    AcliGrantSyncError,
    AcliMarketNotConfiguredError,
    AcliMarketPage,
    AcliMarketUnavailableError,
    AcliOperation,
    AcliOperationCancelledError,
    AcliOperationFailedError,
    AcliOperationInProgressError,
    AcliOperationNotActiveError,
    AcliOperationNotFoundError,
    AcliOutcome,
    AcliRequestRejectedError,
    AcliStageNotAllowedError,
    AcliTool,
    BotCli,
    OperationKind,
    OperationStatus,
    OperationStep,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.service_bot.types import PublishStage

pytestmark = pytest.mark.asyncio


def _request(entity_id: str = "owner") -> Request:
    repository = Mock()
    repository.list_matches_by_legacy_locator.side_effect = lambda bot_id, locator: [{
        "id": 1, "bot_id": bot_id, "entity_id": entity_id, "owner_id": locator,
    }]
    app = FastAPI()
    app.state.injector = SimpleNamespace(get=lambda _contract: repository)
    request = Request({"type": "http", "method": "POST", "path": "/",
                       "app": app, "query_string": b""})
    request.state.trace_id = "trace-acli"
    return request


def _locks(epoch: int | None = 9, holder: str = "owner") -> Mock:
    locks = Mock()
    locks.get_lock_info.return_value = SimpleNamespace(
        lock=None if epoch is None else SimpleNamespace(id=epoch, holder_user_id=holder)
    )
    return locks


def _body(response) -> dict:
    return json.loads(response.body)


def _op(status: str = "succeeded", **fields) -> AcliOperation:
    values = {
        "id": "a" * 32, "kind": "operation", "action": "install", "cli_code": "deepinsight-cli",
        "stage": "draft", "status": status, "step": "installing" if status == "running" else None,
        **fields,
    }
    return AcliOperation(
        id=values["id"], kind=OperationKind(values["kind"]), action=AcliAction(values["action"]),
        cli_code=values["cli_code"], stage=PublishStage(values["stage"]),
        status=OperationStatus(values["status"]),
        step=OperationStep(values["step"]) if values["step"] else None,
        error_code=values.get("error_code"), error_message=values.get("error_message"),
    )


_OP_JSON = {
    "operation_id": "a" * 32, "kind": "operation", "action": "install",
    "cli_code": "deepinsight-cli", "stage": "draft", "status": "running", "step": "installing",
    "error_code": None, "error_message": None,
}


async def test_routes_and_bars() -> None:
    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()["paths"]

    assert set(paths["/openapi/v1/bots/{bot_id}/clis"]) == {"get", "post"}
    assert "/openapi/v1/bots/{bot_id}/clis/market" not in paths
    assert set(paths["/openapi/v1/bots/{bot_id}/clis/{cli_code}"]) == {"delete"}
    assert set(paths["/openapi/v1/bots/{bot_id}/clis/{cli_code}/install/cancel"]) == {"post"}
    assert set(paths["/openapi/v1/bots/{bot_id}/clis/{cli_code}/uninstall/cancel"]) == {"post"}
    app.include_router(operations_router)
    paths = app.openapi()["paths"]
    assert set(paths["/openapi/v1/bots/{bot_id}/cli-operations"]) == {"get"}
    assert set(paths["/openapi/v1/bots/{bot_id}/cli-operations/{operation_id}"]) == {"get"}
    for key in (
        ("GET", "/openapi/v1/bots/{bot_id}/clis"),
        ("GET", "/openapi/v1/bots/{bot_id}/cli-operations"),
        ("GET", "/openapi/v1/bots/{bot_id}/cli-operations/{operation_id}"),
    ):
        assert (AUTHORIZATION[key].level, AUTHORIZATION[key].edit_lock) == (
            PermissionLevel.MEMBER, None,
        )
    for key in (
        ("POST", "/openapi/v1/bots/{bot_id}/clis"),
        ("DELETE", "/openapi/v1/bots/{bot_id}/clis/{cli_code}"),
        ("POST", "/openapi/v1/bots/{bot_id}/clis/{cli_code}/install/cancel"),
        ("POST", "/openapi/v1/bots/{bot_id}/clis/{cli_code}/uninstall/cancel"),
    ):
        assert (AUTHORIZATION[key].level, AUTHORIZATION[key].edit_lock) == (
            PermissionLevel.OWNER, EDIT_LOCK,
        )


async def test_list_returns_what_the_runtime_has() -> None:
    service = AsyncMock()
    service.list_bot_clis.return_value = [
        BotCli("dataphin", "dataphin-cli", "Dataphin", "owner", True),
        BotCli("adev-cli", "adev-cli", "", "caller", False),
    ]

    response = await list_bot_clis(
        "bot-1", _request(), "owner", "owner", RuntimeStage.VERIFY, service
    )

    assert [(i.cli_code, i.identity_mode.value, i.default) for i in response.data] == [
        ("dataphin", "owner", True),
        ("adev-cli", "caller", False),
    ]
    service.list_bot_clis.assert_awaited_once_with(
        bot_id="bot-1", entity_id="owner", actor_id="owner", stage="verify"
    )


async def test_market_search_is_not_bot_scoped_and_passes_paging() -> None:
    app = FastAPI()
    app.include_router(market_router)
    assert set(app.openapi()["paths"]["/openapi/v1/bots/market/clis"]) == {"get"}
    rule = AUTHORIZATION[("GET", "/openapi/v1/bots/market/clis")]
    assert rule.__class__.__name__ == "NoCheck"

    service = AsyncMock()
    service.search_market.return_value = AcliMarketPage(
        (AcliTool("dataphin", "dataphin", "d", "1.4.14"),), 7, True
    )

    response = await search_acli_market(_request(), "data", 2, 5, service)

    assert response.data.items[0].name == "dataphin"
    assert (response.data.total, response.data.has_more) == (7, True)
    service.search_market.assert_awaited_once_with(keyword="data", page_no=2, page_size=5)


@pytest.mark.parametrize(
    ("error", "status"),
    [(AcliMarketNotConfiguredError(), 501), (AcliMarketUnavailableError(), 502)],
)
async def test_market_errors_map_to_their_status(error, status) -> None:
    service = AsyncMock()
    service.search_market.side_effect = error

    response = await search_acli_market(_request(), "", 1, 20, service)

    assert response.status_code == status


async def test_add_passes_the_current_users_lock_epoch() -> None:
    service = AsyncMock()
    service.add.return_value = AcliOutcome(
        operation=_op(), tool=AcliTool("deepinsight-cli", "di", "DI", "1.0.9")
    )

    response = await add_bot_cli(
        "bot-1", AddBotCli(cli_code="deepinsight-cli"), _request(), "owner", "owner",
        None, False, service, _locks(38),
    )

    assert (response.data.cli_code, response.data.command, response.data.version) == (
        "deepinsight-cli", "di", "1.0.9",
    )
    service.add.assert_awaited_once_with(
        bot_id="bot-1", entity_id="owner", actor_id="owner",
        cli_code="deepinsight-cli", lock_epoch=38, stage=None, run_async=False,
    )


async def test_a_running_add_is_202_with_the_operation() -> None:
    service = AsyncMock()
    service.add.return_value = AcliOutcome(operation=_op("running"))

    response = await add_bot_cli(
        "bot-1", AddBotCli(cli_code="deepinsight-cli"), _request(), "owner", "owner",
        RuntimeStage.ONLINE, True, service, _locks(),
    )

    assert response.status_code == 202
    assert _body(response)["data"] == _OP_JSON
    kwargs = service.add.await_args.kwargs
    assert (kwargs["stage"], kwargs["run_async"]) == ("online", True)


@pytest.mark.parametrize(
    "locks", [_locks(None), _locks(38, holder="someone-else"), _locks(True)]  # type: ignore[arg-type]
)
async def test_a_lock_the_caller_does_not_hold_is_not_passed(locks) -> None:
    service = AsyncMock()
    service.remove.return_value = AcliOutcome(operation=_op())

    await remove_bot_cli(
        "bot-1", "yuque-cli", _request(), "owner", "owner", None, False, service, locks
    )

    assert service.remove.await_args.kwargs["lock_epoch"] is None


async def test_remove_answers_deleted_or_running() -> None:
    service = AsyncMock()
    service.remove.return_value = AcliOutcome(operation=_op(action="uninstall"))

    response = await remove_bot_cli(
        "bot-1", "yuque-cli", _request(), "owner", "owner", None, False, service, _locks(4)
    )

    assert response.data.deleted is True
    service.remove.assert_awaited_once_with(
        bot_id="bot-1", entity_id="owner", actor_id="owner", cli_code="yuque-cli",
        lock_epoch=4, stage=None, run_async=False,
    )
    service.remove.return_value = AcliOutcome(operation=_op("running"))
    response = await remove_bot_cli(
        "bot-1", "yuque-cli", _request(), "owner", "owner", None, True, service, _locks(4)
    )
    assert response.status_code == 202


@pytest.mark.parametrize(
    ("handler", "action"),
    [(cancel_bot_cli_install, "install"), (cancel_bot_cli_uninstall, "uninstall")],
)
async def test_cancel_answers_with_the_cancel_operation(handler, action) -> None:
    service = AsyncMock()
    service.cancel.return_value = AcliOutcome(operation=_op(kind="cancel", action=action))

    response = await handler(
        "bot-1", "yuque-cli", _request(), "owner", "owner", None, False, service, _locks(4)
    )

    assert (response.data.kind, response.data.status) == ("cancel", "succeeded")
    assert service.cancel.await_args.kwargs["action"] == AcliAction(action)
    service.cancel.return_value = AcliOutcome(operation=_op("running", kind="cancel"))
    response = await handler(
        "bot-1", "yuque-cli", _request(), "owner", "owner", None, True, service, _locks(4)
    )
    assert response.status_code == 202


async def test_operations_read_back() -> None:
    service = AsyncMock()
    service.get_operation.return_value = _op("failed", error_code="install_failed",
                                             error_message="no such cli")
    service.live_operation.return_value = _op("running")

    one = await get_cli_operation("bot-1", "a" * 32, _request(), "owner", "owner", service)
    live = await get_live_cli_operation("bot-1", _request(), "owner", "owner", None, service)

    assert (one.data.status, one.data.error_code) == ("failed", "install_failed")
    assert live.data.operation_id == "a" * 32
    service.get_operation.assert_awaited_once_with(
        bot_id="bot-1", entity_id="owner", actor_id="owner", operation_id="a" * 32
    )
    service.live_operation.assert_awaited_once_with(
        bot_id="bot-1", entity_id="owner", actor_id="owner", stage=None
    )


@pytest.mark.parametrize(
    ("error", "status", "data"),
    [
        (AcliDefaultCliError("x"), 409, None),
        (AcliStageNotAllowedError(), 422, None),
        (AcliOperationInProgressError(_op("running")), 409, {"operation": _OP_JSON}),
        (AcliOperationNotActiveError(), 409, None),
        (AcliOperationNotFoundError("x"), 404, None),
        (AcliOperationCancelledError("cancelled", "stopped"), 409,
         {"code": "cancelled", "reason": "stopped"}),
        (AcliCliNameMismatchError("di", "deepinsight-cli"), 422, {"market_name": "deepinsight-cli"}),
        (AcliEngineUnsupportedError(), 501, None),
        (AcliEngineOutdatedError("engine_outdated", "old build"), 409,
         {"code": "engine_outdated", "reason": "old build"}),
        (AcliBotNotRunningError("not_running", "no container"), 409,
         {"code": "not_running", "reason": "no container"}),
        (AcliRequestRejectedError("install_failed", "no such cli"), 422,
         {"code": "install_failed", "reason": "no such cli"}),
        (AcliOperationFailedError("timeout", "slow"), 502, {"code": "timeout", "reason": "slow"}),
        (AcliGrantSyncError("grant_failed", "not kept"), 502,
         {"code": "grant_failed", "reason": "not kept"}),
    ],
)
async def test_domain_errors_map_to_their_status_and_data(error, status, data) -> None:
    service = AsyncMock()
    service.add.side_effect = error

    response = await add_bot_cli(
        "bot-1", AddBotCli(cli_code="x"), _request(), "owner", "owner", None, False,
        service, _locks(),
    )

    assert response.status_code == status
    assert _body(response).get("data") == data



@pytest.mark.parametrize("code", ["@antcli/yuntu-cli", "-rf", "a b", ""])
def test_add_refuses_codes_delete_could_not_address(code) -> None:
    """Every code POST accepts must be removable by ``DELETE …/clis/{cli_code}``."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AddBotCli(cli_code=code)
