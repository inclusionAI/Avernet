"""Endpoint coverage for the internal ``/api/bots/.../clis`` routes.

The same real router, service, task queue, Passport scope writer and engine
port as the ``/openapi/v1`` cases in ``test_acli_clis_router.py``, reached as an
authenticated internal user (``x-user-id``). The test world runs no task
worker, so writes are asked for ``async``; running operations to their end is
pinned by the service tests in ``tests/community/core/acli_clis``.
"""

from __future__ import annotations

from typing import Annotated

import httpx

from agentclaw.community.di import config as cfg
from agentclaw.community.plugin_api.http_client import QUALIFIER_GENERAL, HttpClient
from agentclaw.community.plugin_api.passport import PassportPlugin, unpack_resource_scope
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)
from tests.community.endpoints.acli_seeds import seed_running_operation
from tests.community.framework.device_seams import install_fake_resolver


_OWNER_ID = "acli_internal_owner"
_BOT_ID = "acli_internal_bot"
_HEADERS = {"x-user-id": _OWNER_ID}
_ENTITY = {"entity_id": _OWNER_ID}
_MARKET_URL = "https://market.example.test/api/v1/cli-tools/search"


def _seed_owner(world) -> None:
    make_staff_user(world, user_id=_OWNER_ID)


def _seed_bot(world, *, codes: tuple[str, ...] = ("dataphin",), bot_type: str = "service") -> None:
    """An active openclaw bot with a running container; Passport keeps what is written."""
    _seed_owner(world)
    make_bot(
        world,
        bot_id=_BOT_ID,
        owner_id=_OWNER_ID,
        bot_type=bot_type,
        status="ACTIVE",
        active_engine="openclaw",
    )
    clis = [{"cli_code": code, "cli_name": code, "cli_desc": "", "identity_mode": "owner"}
            for code in codes]

    def update_passport(*_args, resource_scope=None, **_kwargs):
        _mcps, written = unpack_resource_scope(resource_scope)
        if written is not None:
            clis[:] = [dict(item) for item in written]

    passport = world.get(PassportPlugin)
    passport.set_override("update_passport", update_passport)
    passport.set_override(
        "query_agent_passport", lambda *_a, **_k: {"mcps": [], "clis": list(clis)}
    )
    install_fake_resolver(world)


def _seed_personal_bot(world) -> None:
    _seed_bot(world, bot_type="personal")


def _seed_bot_with_added_cli(world) -> None:
    _seed_bot(world, codes=("dataphin", "yuque-cli"))


def _seed_bot_and_market(world) -> None:
    _seed_bot(world)
    _seed_market(world)


def _seed_market(world) -> None:
    _seed_owner(world)
    world.injector.binder.bind(
        cfg.AcliMarketConfig, to=cfg.AcliMarketConfig(search_url=_MARKET_URL), scope=None
    )
    world.get(Annotated[HttpClient, QUALIFIER_GENERAL]).set_response(
        "get",
        httpx.Response(
            200,
            json={"success": True, "errorMsg": None, "data": [
                {"id": 1, "name": "deepinsight-cli", "command": "di",
                 "description": "1", "version": "1.0.9"},
                {"id": 2, "name": "dataphin", "command": "dataphin",
                 "description": "dataphinCLI工具", "version": "1.4.14"},
            ]},
            request=httpx.Request("GET", _MARKET_URL),
        ),
    )


@endpoint_test(
    method="GET",
    path="/api/bots/market/clis",
    scenario="happy",
    input=CaseInput(query_params={"keyword": "d", "page_size": 1}, headers=_HEADERS),
    seed=_seed_market,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "items": [{"name": "deepinsight-cli", "command": "di",
                       "description": "1", "version": "1.0.9"}],
            "total": 2,
            "has_more": True,
        },
    ),
)
def internal_search_acli_market_happy():
    """``market`` is a fixed path, not a bot id; the backend asks the configured market."""


@endpoint_test(
    method="GET",
    path="/api/bots/market/clis",
    scenario="error",
    input=CaseInput(query_params={"keyword": "d"}, headers=_HEADERS),
    seed=_seed_owner,
    expect=ExpectError(status=501),
)
def internal_search_acli_market_not_configured():
    """A deployment with no market URL refuses the search."""


@endpoint_test(
    method="GET",
    path="/api/bots/{bot_id}/clis",
    scenario="happy",
    input=CaseInput(path_params={"bot_id": _BOT_ID}, query_params=_ENTITY, headers=_HEADERS),
    seed=_seed_bot_with_added_cli,
    expect=ExpectSuccess(status=200),
    extra_assertions=(
        lambda response, _world: _assert_default_listed(response, "dataphin"),
    ),
)
def internal_list_bot_clis_happy():
    """The container's tools plus the defaults, flagged."""


@endpoint_test(
    method="GET",
    path="/api/bots/{bot_id}/clis",
    scenario="error",
    input=CaseInput(path_params={"bot_id": _BOT_ID}, headers=_HEADERS),
    seed=_seed_bot,
    expect=ExpectError(status=422),
)
def internal_list_bot_clis_requires_entity():
    """``entity_id`` is required: the bot is never picked by id alone."""


def _seed_bot_and_stranger(world) -> None:
    _seed_bot(world)
    make_staff_user(world, user_id="acli_internal_stranger")


@endpoint_test(
    method="GET",
    path="/api/bots/{bot_id}/clis",
    scenario="not_a_member",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_ENTITY,
        headers={"x-user-id": "acli_internal_stranger"},
    ),
    seed=_seed_bot_and_stranger,
    expect=ExpectError(status=403),
)
def internal_list_bot_clis_refuses_a_non_member():
    """Knowing a bot's id and owner is not permission to read its CLIs."""


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/clis",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_ENTITY | {"async": "true"},
        headers=_HEADERS,
        json_body={"cli_code": "deepinsight-cli"},
    ),
    seed=_seed_personal_bot,
    expect=ExpectSuccess(
        status=202,
        json_contains={"cli_code": "deepinsight-cli", "action": "install",
                       "stage": "draft", "status": "running"},
    ),
)
def internal_add_bot_cli_happy():
    """Enqueued for the bot's one runtime; a personal bot qualifies."""


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/clis",
    scenario="error",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_ENTITY,
        headers=_HEADERS,
        json_body={"cli_code": "di"},
    ),
    seed=_seed_bot_and_market,
    expect=ExpectError(status=422),
)
def internal_add_bot_cli_alias():
    """A command alias is refused before anything is installed."""


@endpoint_test(
    method="DELETE",
    path="/api/bots/{bot_id}/clis/{cli_code}",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "cli_code": "yuque-cli"},
        query_params=_ENTITY | {"async": "true"},
        headers=_HEADERS,
    ),
    seed=_seed_bot_with_added_cli,
    expect=ExpectSuccess(
        status=202, json_contains={"cli_code": "yuque-cli", "action": "uninstall"}
    ),
)
def internal_remove_bot_cli_happy():
    """Enqueued; the grant is never revoked."""


@endpoint_test(
    method="GET",
    path="/api/bots/{bot_id}/cli-operations",
    scenario="happy",
    input=CaseInput(path_params={"bot_id": _BOT_ID}, query_params=_ENTITY, headers=_HEADERS),
    seed=_seed_bot,
    expect=ExpectSuccess(status=200, json_equals=[]),
)
def internal_list_cli_operations_happy():
    """Nothing is running in the runtime."""


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/clis/{cli_code}/{action}/cancel",
    scenario="error",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "cli_code": "yuque-cli", "action": "install"},
        query_params=_ENTITY,
        headers=_HEADERS,
    ),
    seed=_seed_bot,
    expect=ExpectError(status=409),
)
def internal_cancel_not_active():
    """Nothing is running to cancel."""


@endpoint_test(
    method="GET",
    path="/api/bots/{bot_id}/cli-operations/{operation_id}",
    scenario="error",
    input=CaseInput(path_params={"bot_id": _BOT_ID, "operation_id": "0" * 32},
                    query_params=_ENTITY, headers=_HEADERS),
    seed=_seed_bot,
    expect=ExpectError(status=404),
)
def internal_get_cli_operation_unknown():
    """An operation id this bot never had."""


@endpoint_test(
    method="DELETE",
    path="/api/bots/{bot_id}/clis/{cli_code}",
    scenario="error",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "cli_code": "dataphin"},
        query_params=_ENTITY,
        headers=_HEADERS,
    ),
    seed=_seed_bot,
    expect=ExpectError(status=409),
)
def internal_remove_default_bot_cli():
    """A default CLI is re-granted at every start, so removing it is refused."""


def _assert_default_listed(response, code: str) -> None:
    items = {item["cli_code"]: item for item in response.json()}
    assert items[code]["default"] is True, response.json()


_RUNNING_ID = "2" * 32


def _seed_running_install(world) -> None:
    _seed_owner(world)
    bot = make_bot(world, bot_id=_BOT_ID, owner_id=_OWNER_ID, bot_type="service",
                   status="ACTIVE", active_engine="openclaw")
    install_fake_resolver(world)
    seed_running_operation(world, bot=bot, action="install", cli_code="yuque-cli",
                           operation_id=_RUNNING_ID)


@endpoint_test(
    method="GET",
    path="/api/bots/{bot_id}/cli-operations/{operation_id}",
    scenario="happy",
    input=CaseInput(path_params={"bot_id": _BOT_ID, "operation_id": _RUNNING_ID},
                    query_params=_ENTITY, headers=_HEADERS),
    seed=_seed_running_install,
    expect=ExpectSuccess(status=200, json_contains={
        "operation_id": _RUNNING_ID, "status": "running", "step": "installing",
    }),
)
def internal_get_cli_operation_happy():
    """A running install reads back by its id."""


@endpoint_test(
    method="GET",
    path="/api/bots/{bot_id}/cli-operations",
    scenario="error",
    input=CaseInput(path_params={"bot_id": _BOT_ID}, headers=_HEADERS),
    seed=_seed_bot,
    expect=ExpectError(status=422),
)
def internal_list_cli_operations_requires_entity():
    """``entity_id`` is required: the bot is never picked by id alone."""


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/clis/{cli_code}/{action}/cancel",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "cli_code": "yuque-cli", "action": "install"},
        query_params=_ENTITY | {"async": "true"},
        headers=_HEADERS,
    ),
    seed=_seed_running_install,
    expect=ExpectSuccess(status=202, json_contains={"kind": "cancel", "status": "running"}),
)
def internal_cancel_happy():
    """A running install's cancel is enqueued and answered at once."""

