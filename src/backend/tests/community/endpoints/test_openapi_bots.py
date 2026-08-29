"""Declarative happy/error coverage for the public Bots surface.

Fifteen bots operations sat on ``coverage_baseline.txt`` as frozen debt for the
reason the routines and resources groups carried: the case runner authenticated
with ``x-user-id``, ``require_principal`` accepts only a gateway-signed token,
and the harness had no minter — so a case could assert nothing but a 401.

That reason has expired. ``test_openapi_session_files.py`` mints a principal in
the test tree, by pointing ``init_principal_verifier_config`` at a local signing
key and handing the runner a token signed with it; ``test_openapi_routines.py``
and ``test_openapi_resources.py`` do the same. This file follows them, so the
bots rows come off the baseline and the coverage gate holds them from here on.

What these cases add over the handler unit tests in
``adapters/http/openapi_v1/bots/`` is the part a direct ``await handler(...)``
structurally cannot reach: the assembled application. The handler suite passes
every dependency by keyword, so FastAPI's dependency wiring and the injector
never run. Here they do, which puts the DI graph, the router mount, path/query
binding, the per-route grant dependency and the envelope on the wire under test.
"""

from __future__ import annotations

import time
from typing import Any

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.data_init_service import DataInitServiceProtocol
from agentclaw.community.api.engine_config_service import EngineConfigServiceProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_OWNER = "bots-owner"
_BOT_ID = "bots-bot"
_ENGINE = "openclaw"
_KEY = "bots-framework-signing-key-at-least-32-bytes"
_BASE_PATH = "/openapi/v1/bots"
_PATH_PARAMS = {"bot_id": _BOT_ID}


class _Secret:
    secret_user = "test"
    secret_value = _KEY


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
                    "subject": {"id": _OWNER, "username": "bots@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}
_FORBIDDEN_QUERY = {"user_id": "another-user"}


def _bot_record() -> dict[str, Any]:
    return {
        "bot_id": _BOT_ID,
        "bot_name": "Renamed Bot",
        "bot_desc": "renamed",
        "owner_id": _OWNER,
        "entity_id": _OWNER,
        "entity_type": "user",
        "active_engine": _ENGINE,
        "bot_type": "personal",
        "status": "ACTIVE",
    }


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_happy_services(world) -> None:
    _seed_verifier(world)
    # Every operation but ``check-name`` resolves the bot through the real
    # ``BotRepository``; REACTIVATING is the one status dormant activation
    # answers idempotently, and personal is the only bot_type this surface owns
    # the lifecycle of.
    make_bot(
        world,
        bot_id=_BOT_ID,
        owner_id=_OWNER,
        bot_type="personal",
        status="REACTIVATING",
        active_engine=_ENGINE,
    )

    bot_repo = world.get(BotRepository)

    def create_bot(_self, **kwargs):
        """Persist the row ``create_bot`` would, without provisioning a device.

        The only collaborator on this path a request cannot drive: allocation
        posts to BaaS, and the local ``HttpClient`` seam refuses an unstubbed
        call. Everything above it stays real — the engine registry check, the
        engine/cluster pairing, the template preflight, the quota preflight,
        the Passport apply and the owner-relationship write all run, and the
        row this writes is a real repository row the response is built from.
        """
        return bot_repo.insert(
            {
                "bot_id": kwargs["bot_id"],
                "bot_name": kwargs.get("bot_name") or kwargs["bot_id"],
                "bot_desc": kwargs.get("bot_desc"),
                "owner_id": kwargs["user_id"],
                "owner_name": kwargs["user_id"],
                "entity_id": kwargs.get("entity_id") or kwargs["user_id"],
                "entity_type": kwargs.get("entity_type") or "staff",
                "creator_id": kwargs["user_id"],
                "bot_type": kwargs.get("bot_type") or "personal",
                "status": "ACTIVE",
                "active_engine": kwargs.get("engine_type") or _ENGINE,
            }
        )

    def update_bot(_self, *_args, **_kwargs):
        return _bot_record()

    def delete_bot(_self, *_args, **_kwargs) -> bool:
        return True

    def restart_bot(_self, *_args, **_kwargs):
        return _bot_record()

    bind_overrides(
        world,
        BotServiceProtocol,
        {
            "create_bot": create_bot,
            "update_bot": update_bot,
            "delete_bot": delete_bot,
            "restart_bot": restart_bot,
        },
    )

    async def read_bot_config(_self, **_kwargs):
        return {"model": "default"}

    async def write_bot_config(_self, **_kwargs) -> None:
        return None

    bind_overrides(
        world,
        EngineConfigServiceProtocol,
        {"read_bot_config": read_bot_config, "write_bot_config": write_bot_config},
    )

    async def trigger_init(_self, **_kwargs):
        return {"bot_id": _BOT_ID, "status": "completed"}

    bind_overrides(world, DataInitServiceProtocol, {"trigger_init": trigger_init})


_CREATE_BODY = {
    "bot_name": "created-bot",
    "bot_desc": "a bot this case creates",
    "engine": _ENGINE,
    "cluster_name": "ACRA",
    "bot_type": "personal",
}
_UPDATE_BODY = {"bot_name": "Renamed Bot", "bot_desc": "renamed"}

#: ``(method, path, input, success_status)`` — one row per operation, reused by
#: both registration loops below.
_HAPPY_CASES = (
    (
        "GET",
        _BASE_PATH,
        CaseInput(query_params=_QUERY, headers=_HEADERS),
        200,
    ),
    (
        "POST",
        _BASE_PATH,
        CaseInput(query_params=_QUERY, headers=_HEADERS, json_body=_CREATE_BODY),
        201,
    ),
    (
        "GET",
        f"{_BASE_PATH}/all",
        CaseInput(query_params=_QUERY, headers=_HEADERS),
        200,
    ),
    (
        "GET",
        f"{_BASE_PATH}/ceiling",
        CaseInput(query_params=_QUERY, headers=_HEADERS),
        200,
    ),
    (
        "GET",
        f"{_BASE_PATH}/{{bot_id}}",
        CaseInput(path_params=_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS),
        200,
    ),
    (
        "PUT",
        f"{_BASE_PATH}/{{bot_id}}",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_QUERY,
            headers=_HEADERS,
            json_body=_UPDATE_BODY,
        ),
        200,
    ),
    (
        "DELETE",
        f"{_BASE_PATH}/{{bot_id}}",
        CaseInput(path_params=_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS),
        200,
    ),
    (
        "GET",
        f"{_BASE_PATH}/{{bot_id}}/status",
        CaseInput(path_params=_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS),
        200,
    ),
    (
        "GET",
        f"{_BASE_PATH}/{{bot_id}}/passport",
        CaseInput(path_params=_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS),
        200,
    ),
    (
        "GET",
        f"{_BASE_PATH}/{{bot_id}}/engine/config",
        CaseInput(path_params=_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS),
        200,
    ),
    (
        "PUT",
        f"{_BASE_PATH}/{{bot_id}}/engine/config",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_QUERY,
            headers=_HEADERS,
            json_body={"model": "default"},
        ),
        200,
    ),
    (
        "POST",
        f"{_BASE_PATH}/{{bot_id}}/activate",
        CaseInput(path_params=_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS),
        200,
    ),
    (
        "POST",
        f"{_BASE_PATH}/{{bot_id}}/restart",
        CaseInput(path_params=_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS),
        200,
    ),
    (
        "POST",
        f"{_BASE_PATH}/{{bot_id}}/data-init",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_QUERY,
            headers=_HEADERS,
            json_body={"force": True},
        ),
        200,
    ),
)


#: The projection each operation owes, keyed by address. A status on its own
#: would still hold if a read answered a stale name, somebody else's owner, or
#: an empty list where the seeded bot belongs — so each case pins the fields
#: that would actually change under those regressions. ``create`` pins no
#: ``bot_id``: the surface mints one, so the id is not knowable here.
_HAPPY_BODIES = {
    ("GET", _BASE_PATH): {
        # Seeded bot has no template row and a NULL space_id, which resolves
        # to the owner's synthetic personal space, so the row carries the
        # template fields as nulls and the owner-view space summary.
        "data": {
            "total": 1,
            "items": [
                {
                    "bot_id": _BOT_ID,
                    "owner_entity_id": _OWNER,
                    "template_type": None,
                    "template_config": None,
                    "space": {
                        "space_id": f"personal:{_OWNER}",
                        "name": "Personal",
                        "kind": "personal",
                    },
                }
            ],
        }
    },
    ("POST", _BASE_PATH): {
        "data": {"bot_name": "created-bot", "owner_entity_id": _OWNER}
    },
    ("GET", f"{_BASE_PATH}/all"): {
        "data": {
            "total": 1,
            "items": [
                {
                    "bot_id": _BOT_ID,
                    "owner_entity_id": _OWNER,
                    "template_type": None,
                    "template_config": None,
                }
            ],
        }
    },
    ("GET", f"{_BASE_PATH}/ceiling"): {"data": {"ceiling": 5}},
    ("GET", f"{_BASE_PATH}/{{bot_id}}"): {
        "data": {"bot_id": _BOT_ID, "owner_entity_id": _OWNER, "bot_type": "personal"}
    },
    ("PUT", f"{_BASE_PATH}/{{bot_id}}"): {
        "data": {"bot_id": _BOT_ID, "bot_name": "Renamed Bot", "bot_desc": "renamed"}
    },
    ("DELETE", f"{_BASE_PATH}/{{bot_id}}"): {"data": {"deleted": True}},
    ("GET", f"{_BASE_PATH}/{{bot_id}}/status"): {"data": {"status": "REACTIVATING"}},
    ("GET", f"{_BASE_PATH}/{{bot_id}}/passport"): {
        "data": {"bot_id": _BOT_ID, "passport_id": f"mock_agent_code_{_BOT_ID}"}
    },
    ("GET", f"{_BASE_PATH}/{{bot_id}}/engine/config"): {"data": {"model": "default"}},
    ("PUT", f"{_BASE_PATH}/{{bot_id}}/engine/config"): {"data": {"model": "default"}},
    ("POST", f"{_BASE_PATH}/{{bot_id}}/activate"): {
        "data": {"bot_id": _BOT_ID, "status": "REACTIVATING"}
    },
    ("POST", f"{_BASE_PATH}/{{bot_id}}/restart"): {"data": {"bot_id": _BOT_ID}},
    ("POST", f"{_BASE_PATH}/{{bot_id}}/data-init"): {
        "data": {"bot_id": _BOT_ID, "status": "in_progress"}
    },
}


for _method, _path, _input, _status in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        input=_input,
        seed=_seed_happy_services,
        expect=ExpectSuccess(
            status=_status, json_contains=_HAPPY_BODIES[(_method, _path)]
        ),
    )(lambda: None)


# The refusal every user-scoped operation on this surface owes: ``user_id``
# names someone other than the caller the principal authenticated. It is raised
# by ``require_user_id`` ahead of the handler, so no service is seeded —
# reaching one would itself be the bug.
for _method, _path, _input, _status in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="forbidden_user_scope",
        input=CaseInput(
            path_params=_input.path_params,
            query_params=_FORBIDDEN_QUERY,
            headers=_HEADERS,
            json_body=_input.json_body,
        ),
        seed=_seed_verifier,
        expect=ExpectError(status=403, json_contains={"data": None}),
    )(lambda: None)


# ``check-name`` is the one operation here with no user dimension — it asks a
# tenant-wide uniqueness question and takes no ``user_id`` — so the refusal
# above has nothing to refuse. Its own error is the one it shares with create
# and update: a name the whole lifecycle rejects is a 400 here rather than a
# false "available".
@endpoint_test(
    method="GET",
    path=f"{_BASE_PATH}/check-name",
    scenario="happy",
    input=CaseInput(query_params={"name": "an-unused-name"}, headers=_HEADERS),
    seed=_seed_happy_services,
    expect=ExpectSuccess(
        status=200, json_contains={"data": {"name": "an-unused-name", "exists": False}}
    ),
)
def check_name_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=f"{_BASE_PATH}/check-name",
    scenario="invalid_name",
    input=CaseInput(query_params={"name": "bad@name"}, headers=_HEADERS),
    seed=_seed_verifier,
    expect=ExpectError(status=400, json_contains={"data": None}),
)
def check_name_invalid():
    """The framework owns invocation."""
