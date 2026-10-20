"""Endpoint coverage for the internal ``/api/bots/.../clis`` routes.

The same real router, service, Passport scope writer and engine port as the
``/openapi/v1`` cases in ``test_acli_clis_router.py``, reached as an
authenticated internal user (``x-user-id``). Ordering, rollback and every
failure branch are pinned by the service tests in ``tests/community/core/acli_clis``.
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
        lambda response, _world: _assert_codes(response, ["dataphin", "yuque-cli"]),
    ),
)
def internal_list_bot_clis_happy():
    """Every grant is listed for the bot's owner."""


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
        query_params=_ENTITY,
        headers=_HEADERS,
        json_body={"cli_code": "deepinsight-cli"},
    ),
    seed=_seed_personal_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={"cli_code": "deepinsight-cli", "command": "di", "version": "1.0.0"},
    ),
)
def internal_add_bot_cli_happy():
    """Installed in the container, then granted; a personal bot qualifies."""


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
    seed=_seed_bot,
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
        query_params=_ENTITY,
        headers=_HEADERS,
    ),
    seed=_seed_bot_with_added_cli,
    expect=ExpectSuccess(status=200, json_contains={"cli_code": "yuque-cli", "deleted": True}),
)
def internal_remove_bot_cli_happy():
    """Revoked, then uninstalled."""


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


def _assert_codes(response, codes: list[str]) -> None:
    assert [item["cli_code"] for item in response.json()] == codes, response.json()
