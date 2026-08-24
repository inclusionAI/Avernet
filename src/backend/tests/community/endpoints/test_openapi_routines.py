"""Declarative happy/error coverage for the public Routines surface.

The seven routines operations were carried on ``coverage_baseline.txt`` as
frozen debt for one stated reason: the case runner authenticates with
``x-user-id``, ``require_principal`` accepts only a gateway-signed token, and
the harness had no minter — so a case could assert nothing but a 401.

That reason has expired. ``test_openapi_session_files.py`` mints a principal
here in the test tree, by pointing ``init_principal_verifier_config`` at a
local signing key and handing the runner a token signed with it. This file
does the same, so the routines rows come off the baseline and the coverage
gate holds them from here on.

What these cases add over ``adapters/http/openapi_v1/routines/`` — which
already covers every handler thoroughly — is the part a direct ``await
handler(...)`` structurally cannot reach: the assembled application. The
handler suite passes ``factory=`` by keyword, so FastAPI's dependency wiring
and the injector never run. Here they do, which is what puts the DI graph,
the router mount, path/query binding and the envelope on the wire under test.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
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

_OWNER = "routine-owner"
_BOT_ID = "routine-bot"
_ROUTINE_ID = "routine-1"
_KEY = "routines-framework-signing-key-at-least-32-bytes"
_BASE_PATH = "/openapi/v1/bots/{bot_id}/routines"
_PATH_PARAMS = {"bot_id": _BOT_ID}
_ROUTINE_PATH_PARAMS = {**_PATH_PARAMS, "routine_id": _ROUTINE_ID}


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
                    "subject": {"id": _OWNER, "username": "routines@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}
_FORBIDDEN_QUERY = {"user_id": "another-user"}


def _routine() -> dict:
    """One routine as the engine adapter returns it, under ``data``."""
    return {
        "id": _ROUTINE_ID,
        "bot_id": _BOT_ID,
        "name": "morning-brief",
        "enabled": True,
        "schedule": {"kind": "cron", "expr": "0 9 * * *", "tz": "Asia/Shanghai"},
        "payload": {"kind": "message", "message": "echo hi"},
        "created_at_ms": 1722165600000,
        "updated_at_ms": 1722165600000,
    }


def _run() -> dict:
    return {
        "job_id": "run-1",
        "started_at_ms": 1722165600000,
        "finished_at_ms": 1722165601500,
        "status": "succeeded",
        "error": "",
        "duration_ms": 1500,
    }


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_happy_services(world) -> None:
    _seed_verifier(world)
    make_bot(world, bot_id=_BOT_ID, owner_id=_OWNER)

    async def list_all_crons(_self, **_kwargs):
        return {"success": True, "data": [_routine()]}

    async def create_cron(_self, **_kwargs):
        return {"success": True, "data": _routine()}

    async def get_cron_detail(_self, **_kwargs):
        return {"success": True, "data": _routine()}

    async def update_cron(_self, **_kwargs):
        return {"success": True, "data": _routine()}

    async def delete_cron(_self, **_kwargs):
        return {"success": True, "data": {"deleted": True}}

    async def run_cron(_self, **_kwargs):
        return {"success": True, "data": {"status": "dispatched", "runId": "run-1"}}

    async def get_cron_runs(_self, **_kwargs):
        return {"success": True, "data": {"runs": [_run()]}}

    bind_overrides(
        world,
        CronRelayServiceProtocol,
        {
            "list_all_crons": list_all_crons,
            "create_cron": create_cron,
            "get_cron_detail": get_cron_detail,
            "update_cron": update_cron,
            "delete_cron": delete_cron,
            "run_cron": run_cron,
            "get_cron_runs": get_cron_runs,
        },
    )


_CREATE_BODY = {
    "name": "morning-brief",
    "trigger": {"cron": "0 9 * * *"},
    "command": "echo hi",
}
_UPDATE_BODY = {"name": "renamed"}

_HAPPY_CASES = (
    (
        "GET",
        _BASE_PATH,
        CaseInput(path_params=_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS),
        200,
    ),
    (
        "POST",
        _BASE_PATH,
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_QUERY,
            headers=_HEADERS,
            json_body=_CREATE_BODY,
        ),
        201,
    ),
    (
        "GET",
        f"{_BASE_PATH}/{{routine_id}}",
        CaseInput(
            path_params=_ROUTINE_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS
        ),
        200,
    ),
    (
        "PATCH",
        f"{_BASE_PATH}/{{routine_id}}",
        CaseInput(
            path_params=_ROUTINE_PATH_PARAMS,
            query_params=_QUERY,
            headers=_HEADERS,
            json_body=_UPDATE_BODY,
        ),
        200,
    ),
    (
        "DELETE",
        f"{_BASE_PATH}/{{routine_id}}",
        CaseInput(
            path_params=_ROUTINE_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS
        ),
        200,
    ),
    (
        "POST",
        f"{_BASE_PATH}/{{routine_id}}/run",
        CaseInput(
            path_params=_ROUTINE_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS
        ),
        200,
    ),
    (
        "GET",
        f"{_BASE_PATH}/{{routine_id}}/runs",
        CaseInput(
            path_params=_ROUTINE_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS
        ),
        200,
    ),
)


for _method, _path, _input, _status in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        input=_input,
        seed=_seed_happy_services,
        expect=ExpectSuccess(status=_status),
    )(lambda: None)


# The refusal every user-scoped operation on this surface owes: ``user_id``
# names someone other than the caller the principal authenticated. It is
# raised by ``require_user_id`` ahead of the handler, so no service is seeded —
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
