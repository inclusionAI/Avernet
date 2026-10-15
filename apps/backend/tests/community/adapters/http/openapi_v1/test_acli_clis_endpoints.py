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
    list_bot_clis,
    remove_bot_cli,
    router,
    search_acli_market,
)
from agentclaw.community.adapters.http.openapi_v1.bots.schemas_acli_clis import AddBotCli
from agentclaw.community.api.acli_cli_service import (
    AcliBotNotRunningError,
    AcliCliAlreadyGrantedError,
    AcliCliNameMismatchError,
    AcliCliNotGrantedError,
    AcliDefaultCliError,
    AcliEngineUnsupportedError,
    AcliGrantSyncError,
    AcliIdentityCleanupError,
    AcliMarketPage,
    AcliOperationFailedError,
    AcliRequestRejectedError,
    AcliRollbackFailedError,
    AcliTool,
    BotCli,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel

pytestmark = pytest.mark.asyncio


def _request() -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/"})
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


async def test_routes_and_bars() -> None:
    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()["paths"]

    assert set(paths["/openapi/v1/bots/{bot_id}/clis"]) == {"get", "post"}
    assert set(paths["/openapi/v1/bots/{bot_id}/clis/market"]) == {"get"}
    assert set(paths["/openapi/v1/bots/{bot_id}/clis/{cli_code}"]) == {"delete"}
    for method in ("GET",):
        rule = AUTHORIZATION[(method, "/openapi/v1/bots/{bot_id}/clis")]
        assert (rule.level, rule.edit_lock) == (PermissionLevel.MEMBER, None)
    for key in (
        ("POST", "/openapi/v1/bots/{bot_id}/clis"),
        ("DELETE", "/openapi/v1/bots/{bot_id}/clis/{cli_code}"),
    ):
        assert (AUTHORIZATION[key].level, AUTHORIZATION[key].edit_lock) == (
            PermissionLevel.OWNER, EDIT_LOCK,
        )


async def test_list_returns_every_grant() -> None:
    service = Mock()
    service.list_bot_clis.return_value = [
        BotCli("dataphin", "dataphin-cli", "Dataphin", "owner", True),
        BotCli("adev-cli", "adev-cli", "", "caller", False),
    ]

    response = await list_bot_clis("bot-1", _request(), "owner", "owner", service)

    assert [(i.cli_code, i.identity_mode.value, i.default) for i in response.data] == [
        ("dataphin", "owner", True),
        ("adev-cli", "caller", False),
    ]
    service.list_bot_clis.assert_called_once_with(bot_id="bot-1", entity_id="owner")


async def test_market_search_passes_paging() -> None:
    service = AsyncMock()
    service.search_market.return_value = AcliMarketPage(
        (AcliTool("dataphin", "dataphin", "d", "1.4.14"),), 7, True
    )

    response = await search_acli_market("bot-1", _request(), "owner", "owner", "data", 2, 5, service)

    assert response.data.items[0].name == "dataphin"
    assert (response.data.total, response.data.has_more) == (7, True)
    service.search_market.assert_awaited_once_with(
        bot_id="bot-1", entity_id="owner", keyword="data", page_no=2, page_size=5
    )


async def test_add_passes_the_current_users_lock_epoch() -> None:
    service = AsyncMock()
    service.add.return_value = AcliTool("deepinsight-cli", "di", "DI", "1.0.9")

    response = await add_bot_cli(
        "bot-1", AddBotCli(cli_code="deepinsight-cli"), _request(), "owner", "owner",
        service, _locks(38),
    )

    assert (response.data.cli_code, response.data.command, response.data.version) == (
        "deepinsight-cli", "di", "1.0.9",
    )
    service.add.assert_awaited_once_with(
        bot_id="bot-1", entity_id="owner", actor_id="owner",
        cli_code="deepinsight-cli", lock_epoch=38,
    )


@pytest.mark.parametrize(
    "locks", [_locks(None), _locks(38, holder="someone-else"), _locks(True)]  # type: ignore[arg-type]
)
async def test_a_lock_the_caller_does_not_hold_is_not_passed(locks) -> None:
    service = AsyncMock()

    await remove_bot_cli("bot-1", "yuque-cli", _request(), "owner", "owner", service, locks)

    assert service.remove.await_args.kwargs["lock_epoch"] is None


async def test_remove_answers_deleted() -> None:
    service = AsyncMock()

    response = await remove_bot_cli(
        "bot-1", "yuque-cli", _request(), "owner", "owner", service, _locks(4)
    )

    assert response.data.deleted is True
    service.remove.assert_awaited_once_with(
        bot_id="bot-1", entity_id="owner", actor_id="owner", cli_code="yuque-cli", lock_epoch=4
    )


@pytest.mark.parametrize(
    ("error", "status", "data"),
    [
        (AcliCliAlreadyGrantedError("x"), 409, None),
        (AcliCliNotGrantedError("x"), 404, None),
        (AcliDefaultCliError("x"), 409, None),
        (AcliCliNameMismatchError("di", "deepinsight-cli"), 422, {"market_name": "deepinsight-cli"}),
        (AcliEngineUnsupportedError(), 501, None),
        (AcliBotNotRunningError("not_running", "no container"), 409,
         {"code": "not_running", "reason": "no container"}),
        (AcliRequestRejectedError("install_failed", "no such cli"), 422,
         {"code": "install_failed", "reason": "no such cli"}),
        (AcliOperationFailedError("timeout", "slow"), 502, {"code": "timeout", "reason": "slow"}),
        (AcliGrantSyncError(), 502, None),
        (AcliRollbackFailedError("x", "installed_not_granted"), 500,
         {"state": "installed_not_granted"}),
        (AcliIdentityCleanupError("x"), 500, {"state": "removed_caller_override_kept"}),
    ],
)
async def test_domain_errors_map_to_their_status_and_data(error, status, data) -> None:
    service = AsyncMock()
    service.add.side_effect = error

    response = await add_bot_cli(
        "bot-1", AddBotCli(cli_code="x"), _request(), "owner", "owner", service, _locks()
    )

    assert response.status_code == status
    assert _body(response).get("data") == data



@pytest.mark.parametrize("code", ["@antcli/yuntu-cli", "-rf", "a b", ""])
def test_add_refuses_codes_delete_could_not_address(code) -> None:
    """Every code POST accepts must be removable by ``DELETE …/clis/{cli_code}``."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AddBotCli(cli_code=code)
