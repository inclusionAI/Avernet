"""Declarative happy/error coverage for the public Sessions surface.

Seven session operations — create, list, get, update, delete, read history and
clear history — sat on ``coverage_baseline.txt`` as frozen debt for the reason
the whole ``/openapi/v1`` group carried: the case runner authenticates with
``x-user-id``, ``require_principal`` accepts only a gateway-signed token, and
the harness had no minter, so a case could assert nothing but a 401.

``test_openapi_session_files.py`` retired that reason for the *files* half of
this very router — it mints a principal inside the test tree by pointing
``init_principal_verifier_config`` at a local signing key. This file does the
same for the session operations themselves, so their rows come off the baseline
and the coverage gate holds them from here on.

What these cases add over ``adapters/http/openapi_v1/engine_runtime/`` — whose
unit tests already drive every handler through a hand-built app — is the
assembled application: the real injector, the real router mount, the real
dependency graph. The unit suite builds its own ``FastAPI()`` and binds the
relay by hand; here the app is the production one and the relay is substituted
*through* the graph, so a binding that does not resolve or a route that is not
mounted fails the case rather than passing it.

The device itself is the one seam these cases stand in for. Every handler here
resolves the bot (``resolve_operable_bot`` → ``relay.resolve_bot_off_loop``)
and then forwards to the engine (``relay.call``); both are overridden, which
leaves the friend-fallback branch out of scope — it is reached only when the
resolve *refuses*, and refusing is exactly what the error cases below assert is
answered earlier, at ``require_user_id``.
"""

from __future__ import annotations

import time
from typing import Any

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.models import BotFacts, EngineResult
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

_OWNER = "session-owner"
_BOT_ID = "session-bot"
_SESSION_ID = "session-1"
_KEY = "sessions-framework-signing-key-at-least-32-bytes"
_BASE_PATH = "/openapi/v1/bots/{bot_id}/sessions"
_PATH_PARAMS = {"bot_id": _BOT_ID}
_SESSION_PATH_PARAMS = {**_PATH_PARAMS, "session_id": _SESSION_ID}


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
                    "subject": {"id": _OWNER, "username": "sessions@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}
_FORBIDDEN_QUERY = {"user_id": "another-user"}


def _session() -> dict[str, Any]:
    """One session exactly as the engine's ``_session_to_dict`` returns it."""
    return {
        "id": _SESSION_ID,
        "title": "morning stand-up",
        "agent_id": "agent-1",
        "model": "claude-opus",
        "permission_mode": "default",
        "cwd": "/workspace",
        "runtime": "openclaw",
        "message_count": 1,
        "gmt_created": "2026-08-01 09:00:00",
        "gmt_modified": "2026-08-01 09:05:00",
    }


def _message() -> dict[str, Any]:
    return {
        "id": "message-1",
        "session_id": _SESSION_ID,
        "role": "user",
        "content": "hello",
        "gmt_created": "2026-08-01 09:00:01",
    }


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_happy_services(world) -> None:
    _seed_verifier(world)
    # ``resolve_bot_off_loop`` is overridden below, but the Bot row is still
    # seeded: this is the world the operations describe, and a case whose
    # only bot lives in an override would pass against a graph that has no
    # bot table wired at all.
    make_bot(world, bot_id=_BOT_ID, owner_id=_OWNER, active_engine="openclaw")

    async def resolve_bot_off_loop(_self, _bot_id, _owner_id, _caller_id):
        return BotFacts(
            bot_id=_BOT_ID,
            bot_type="personal",
            active_engine="openclaw",
            owner_id=_OWNER,
        )

    async def call(
        _self,
        *,
        bot_id,
        owner_id,
        method,
        path,
        body=None,
        params=None,
        timeout=None,
        enveloped=True,
        facts=None,
        stage,
    ) -> EngineResult:
        """Answer as the bot's engine adapter would, per forwarded route.

        Keyed on the engine path each handler builds, so a handler that
        forwarded the wrong route would fall through to the empty result and
        fail its case rather than being served a session regardless.
        """
        if path == "/api/sessions":
            # GET lists, POST creates — the engine answers a list for one and
            # the created record for the other.
            return EngineResult(data=[_session()] if method == "GET" else _session())
        if path == f"/api/sessions/{_SESSION_ID}":
            # GET reads; DELETE removes and carries no payload.
            return EngineResult(data=_session() if method == "GET" else None)
        if path == f"/api/sessions/{_SESSION_ID}/update":
            return EngineResult(data=_session())
        if path == f"/api/sessions/{_SESSION_ID}/messages":
            return EngineResult(data=[_message()] if method == "GET" else None)
        return EngineResult(data=None)

    bind_overrides(
        world,
        EngineRuntimeRelayProtocol,
        {
            "resolve_bot_off_loop": resolve_bot_off_loop,
            "call": call,
        },
    )


_CREATE_BODY = {"title": "morning stand-up", "model": "claude-opus"}
_UPDATE_BODY = {"title": "renamed"}

#: What a mapped session looks like on the wire, so a case cannot pass on a
#: bare 200 that carried nothing.
_SESSION_BODY = {
    "session_id": _SESSION_ID,
    "title": "morning stand-up",
    "model": "claude-opus",
    "runtime": "openclaw",
}
_DELETED_BODY = {"data": {"deleted": True}}

#: ``(method, path, input, status, json_contains)``.
_HAPPY_CASES = (
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
        {"data": _SESSION_BODY},
    ),
    (
        "GET",
        _BASE_PATH,
        CaseInput(path_params=_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS),
        200,
        {"data": {"total": 1, "items": [_SESSION_BODY]}},
    ),
    (
        "GET",
        f"{_BASE_PATH}/{{session_id}}",
        CaseInput(
            path_params=_SESSION_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS
        ),
        200,
        {"data": _SESSION_BODY},
    ),
    (
        "PATCH",
        f"{_BASE_PATH}/{{session_id}}",
        CaseInput(
            path_params=_SESSION_PATH_PARAMS,
            query_params=_QUERY,
            headers=_HEADERS,
            json_body=_UPDATE_BODY,
        ),
        200,
        {"data": _SESSION_BODY},
    ),
    (
        "DELETE",
        f"{_BASE_PATH}/{{session_id}}",
        CaseInput(
            path_params=_SESSION_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS
        ),
        200,
        _DELETED_BODY,
    ),
    (
        "GET",
        f"{_BASE_PATH}/{{session_id}}/messages",
        CaseInput(
            path_params=_SESSION_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS
        ),
        200,
        {
            "data": {
                "total": 1,
                "items": [
                    {
                        "message_id": "message-1",
                        "session_id": _SESSION_ID,
                        "role": "user",
                        "content": "hello",
                    }
                ],
            }
        },
    ),
    (
        "DELETE",
        f"{_BASE_PATH}/{{session_id}}/messages",
        CaseInput(
            path_params=_SESSION_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS
        ),
        200,
        _DELETED_BODY,
    ),
)


for _method, _path, _input, _status, _contains in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        input=_input,
        seed=_seed_happy_services,
        expect=ExpectSuccess(status=_status, json_contains=_contains),
    )(lambda: None)


# The refusal every user-scoped operation on this surface owes: ``user_id``
# names someone other than the caller the principal authenticated. It is raised
# by ``require_user_id`` ahead of the handler, so neither the bot nor the relay
# is seeded — reaching either would itself be the bug.
for _method, _path, _input, _status, _contains in _HAPPY_CASES:
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
