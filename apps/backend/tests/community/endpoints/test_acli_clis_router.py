"""Endpoint coverage for ``/openapi/v1/bots/{bot_id}/clis``.

The cases run the real router, service, task queue, Passport scope writer and
engine port. The bot's container is the in-memory adapter's simulated acli
install target, reached through a fake device resolver; Passport is the test
profile's recording fake. The market search reaches the test profile's stubbed
HTTP client at a configured URL. The test world runs no task worker, so writes
are asked for ``async`` and answered with the operation they enqueued; running
operations to their end is pinned by the service tests in
``tests/community/core/acli_clis``.
"""

from __future__ import annotations

import time
from typing import Annotated

import httpx

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.di import config as cfg
from agentclaw.community.plugin_api.http_client import QUALIFIER_GENERAL, HttpClient
from agentclaw.community.plugin_api.passport import PassportPlugin, unpack_resource_scope
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)
from tests.community.framework.device_seams import install_fake_resolver


_OWNER_ID = "acli_clis_owner"
_BOT_ID = "acli_clis_bot"
_PRINCIPAL_KEY = "acli-clis-openapi-signing-key-0032b"


class _Secret:
    secret_user = "test"
    secret_value = _PRINCIPAL_KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {"id": _OWNER_ID, "username": "acli@example.test"},
                }
            ],
        },
        _PRINCIPAL_KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER_ID}


def _seed_owner(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    make_staff_user(world, user_id=_OWNER_ID)


def _seed_bot(world, *, clis: list[str] | None = None) -> None:
    """An active openclaw service bot with a running container and these grants."""
    _seed_owner(world)
    make_bot(
        world,
        bot_id=_BOT_ID,
        owner_id=_OWNER_ID,
        bot_type="service",
        status="ACTIVE",
        active_engine="openclaw",
    )
    world.get(PassportPlugin).set_response(
        "query_agent_passport",
        {
            "mcps": [],
            "clis": [
                {"cli_code": code, "cli_name": code, "cli_desc": "", "identity_mode": "owner"}
                for code in (clis if clis is not None else ["dataphin"])
            ],
        },
    )
    install_fake_resolver(world)


def _seed_bot_with_added_cli(world) -> None:
    _seed_bot(world, clis=["dataphin", "yuque-cli"])


def _seed_stateful_bot_with_added_cli(world) -> None:
    _seed_bot_with_stateful_passport(world, ("dataphin", "yuque-cli"))


def _seed_bot_with_stateful_passport(world, codes: tuple[str, ...] = ("dataphin",)) -> None:
    """Passport keeps what is written, so a write's read-back sees its effect."""
    _seed_bot(world)
    passport = world.get(PassportPlugin)
    clis = [{"cli_code": code, "cli_name": code, "cli_desc": "", "identity_mode": "owner"}
            for code in codes]

    def update_passport(*_args, resource_scope=None, **_kwargs):
        _mcps, written = unpack_resource_scope(resource_scope)
        if written is not None:
            clis[:] = [dict(item) for item in written]

    passport.set_override("update_passport", update_passport)
    passport.set_override(
        "query_agent_passport", lambda *_a, **_k: {"mcps": [], "clis": list(clis)}
    )


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/clis",
    scenario="happy",
    input=CaseInput(path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS),
    seed=_seed_bot_with_added_cli,
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
    extra_assertions=(lambda response, _world: _assert_default_listed(response, "dataphin"),),
)
def list_bot_clis_happy():
    """The container's tools plus the bot's defaults, flagged."""


def _assert_default_listed(response, code: str) -> None:
    items = {item["cli_code"]: item for item in response.json()["data"]}
    assert items[code] == {"cli_code": code, "cli_name": code, "cli_desc": "",
                           "identity_mode": "owner", "default": True}, response.json()


def _seed_personal_bot(world) -> None:
    _seed_owner(world)
    make_bot(world, bot_id=_BOT_ID, owner_id=_OWNER_ID, bot_type="personal",
             status="ACTIVE", active_engine="openclaw")
    install_fake_resolver(world)


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/clis",
    scenario="stage_on_personal_bot",
    input=CaseInput(path_params={"bot_id": _BOT_ID}, query_params=_QUERY | {"stage": "draft"},
                    headers=_HEADERS),
    seed=_seed_personal_bot,
    expect=ExpectError(status=422, json_contains={"message": "Only service bots take a stage"}),
)
def list_bot_clis_stage_on_personal_bot():
    """A personal bot has one runtime; naming a stage for it is refused."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/clis",
    scenario="error",
    input=CaseInput(path_params={"bot_id": "missing_acli_bot"}, query_params=_QUERY, headers=_HEADERS),
    seed=_seed_owner,
    expect=ExpectError(status=404, json_contains={"message": "Not found", "data": None}),
)
def list_bot_clis_missing_bot():
    """An absent bot reads as the standard 404."""


_MARKET_URL = "https://market.example.test/api/v1/cli-tools/search"


def _seed_market(world) -> None:
    """A configured market whose API answers with two published tools."""
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
                 "description": "1", "version": "1.0.9", "readme": "# DI"},
                {"id": 2, "name": "dataphin", "command": "dataphin",
                 "description": "dataphinCLI工具", "version": "1.4.14"},
            ]},
            request=httpx.Request("GET", _MARKET_URL),
        ),
    )


def _seed_bot_and_market(world) -> None:
    _seed_market(world)
    _seed_bot(world)


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/market/clis",
    scenario="happy",
    input=CaseInput(query_params={"keyword": "d", "page_size": 1}, headers=_HEADERS),
    seed=_seed_market,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "items": [{"name": "deepinsight-cli", "command": "di",
                           "description": "1", "version": "1.0.9"}],
                "total": 2,
                "has_more": True,
            },
        },
    ),
)
def search_acli_market_happy():
    """The backend asks the configured market API; no bot or container is involved."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/market/clis",
    scenario="error",
    input=CaseInput(query_params={"keyword": "d"}, headers=_HEADERS),
    seed=_seed_owner,
    expect=ExpectError(
        status=501, json_contains={"message": "The acli market is not configured", "data": None}
    ),
)
def search_acli_market_not_configured():
    """A deployment with no market URL refuses the search plainly."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/clis",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY | {"async": "true"},
        headers=_HEADERS,
        json_body={"cli_code": "deepinsight-cli"},
    ),
    seed=_seed_bot_with_stateful_passport,
    expect=ExpectSuccess(
        status=202,
        json_contains={
            "data": {"kind": "operation", "action": "install", "cli_code": "deepinsight-cli",
                     "stage": "draft", "status": "running", "step": "granting"},
        },
    ),
)
def add_bot_cli_happy():
    """Enqueued: the operation answers at once, about to grant and then install."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/clis/{cli_code}/install/cancel",
    scenario="error",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "cli_code": "deepinsight-cli"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_bot,
    expect=ExpectError(status=409, json_contains={"message": "No such CLI operation is running"}),
)
def cancel_bot_cli_install_not_active():
    """Nothing is running to cancel."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/cli-operations",
    scenario="happy",
    input=CaseInput(path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS),
    seed=_seed_bot,
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": []}),
)
def list_cli_operations_happy():
    """No operation is running in the runtime."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/cli-operations/{operation_id}",
    scenario="error",
    input=CaseInput(path_params={"bot_id": _BOT_ID, "operation_id": "0" * 32},
                    query_params=_QUERY, headers=_HEADERS),
    seed=_seed_bot,
    expect=ExpectError(status=404, json_contains={"message": "Not found", "data": None}),
)
def get_cli_operation_unknown():
    """An operation id this bot never had reads as the standard 404."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/clis",
    scenario="error",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"cli_code": "di"},
    ),
    seed=_seed_bot_and_market,
    expect=ExpectError(
        status=422,
        json_contains={
            "message": "Use the tool's acli market name",
            "data": {"market_name": "deepinsight-cli"},
        },
    ),
)
def add_bot_cli_alias():
    """A command alias is refused with the market name to use instead."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/clis/{cli_code}",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "cli_code": "yuque-cli"},
        query_params=_QUERY | {"async": "true", "stage": "verify"},
        headers=_HEADERS,
    ),
    seed=_seed_stateful_bot_with_added_cli,
    expect=ExpectSuccess(
        status=202,
        json_contains={"data": {"action": "uninstall", "cli_code": "yuque-cli",
                                "stage": "verify", "status": "running"}},
    ),
)
def remove_bot_cli_happy():
    """Enqueued for the verify runtime; the grant is never revoked."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/clis/{cli_code}",
    scenario="error",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "cli_code": "dataphin"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_bot,
    expect=ExpectError(
        status=409, json_contains={"message": "Default CLIs cannot be removed", "data": None}
    ),
)
def remove_default_bot_cli():
    """A default CLI is re-granted at every start, so removing it is refused."""
