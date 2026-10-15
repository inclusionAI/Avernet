"""Endpoint coverage for ``/openapi/v1/bots/{bot_id}/clis``.

The cases run the real router, service, Passport scope writer and engine port.
The bot's container is the in-memory adapter's simulated acli market, reached
through a fake device resolver; Passport is the test profile's recording fake.
Ordering, rollback and every failure branch are pinned by the service tests in
``tests/community/core/acli_clis``.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
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


def _seed_bot_with_stateful_passport(world) -> None:
    """Passport keeps what is written, so the add's read-back finds its grant."""
    _seed_bot(world)
    passport = world.get(PassportPlugin)
    clis = [{"cli_code": "dataphin", "cli_name": "dataphin", "cli_desc": "",
             "identity_mode": "owner"}]

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
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": [
                {"cli_code": "dataphin", "cli_name": "dataphin", "cli_desc": "",
                 "identity_mode": "owner", "default": True},
                {"cli_code": "yuque-cli", "cli_name": "yuque-cli", "cli_desc": "",
                 "identity_mode": "owner", "default": False},
            ],
        },
    ),
)
def list_bot_clis_happy():
    """Every grant is listed; the default profile's CLI is flagged."""


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


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/clis/market",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={**_QUERY, "keyword": "deep"},
        headers=_HEADERS,
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "items": [{"name": "deepinsight-cli", "command": "di",
                           "description": "", "version": "1.0.0"}],
                "total": 1,
                "has_more": False,
            },
        },
    ),
)
def search_acli_market_happy():
    """The search runs in the bot's container and returns the market's page."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/clis/market",
    scenario="error",
    input=CaseInput(path_params={"bot_id": "missing_acli_bot"}, query_params=_QUERY, headers=_HEADERS),
    seed=_seed_owner,
    expect=ExpectError(status=404, json_contains={"message": "Not found", "data": None}),
)
def search_acli_market_missing_bot():
    """Searching needs a bot to search from."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/clis",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"cli_code": "deepinsight-cli"},
    ),
    seed=_seed_bot_with_stateful_passport,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"cli_code": "deepinsight-cli", "command": "di", "version": "1.0.0"},
        },
    ),
)
def add_bot_cli_happy():
    """Installed in the container, then granted."""


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
    seed=_seed_bot,
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
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_bot_with_added_cli,
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": {"deleted": True}}),
)
def remove_bot_cli_happy():
    """Revoked, then uninstalled."""


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
