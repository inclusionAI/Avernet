"""Declarative happy/error coverage for the public bot-identity surface.

The three identity operations sat on ``coverage_baseline.txt`` for the one
reason every ``/openapi/v1`` entry there blames: the case runner authenticates
with ``x-user-id``, ``require_principal`` accepts only a gateway-signed token,
and the harness had no minter — so a case could assert nothing but a 401.

That reason expired when ``test_openapi_session_files.py`` minted a principal
inside the test tree: point ``init_principal_verifier_config`` at a local
signing key and hand the runner a token signed with it.
``test_openapi_routines.py`` and ``test_openapi_resources.py`` do the same, and
this file follows them, so the identity rows come off the baseline and the
coverage gate holds them from here on.

What these cases add over ``adapters/http/openapi_v1/`` — where the handlers
are already covered — is the part a direct ``await handler(...)`` structurally
cannot reach: the assembled application. The handler suites pass every
dependency by keyword, so FastAPI's wiring and the injector never run. Here
they do, which puts the DI graph, the router mount, the ``{file_type}`` enum
binding and the envelope on the wire under test.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.config_compose.teclaw_paths import IDENTITY_NS
from agentclaw.community.core.services.identity import IdentityService
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

_OWNER = "identity-owner"
_BOT_ID = "identity-bot"
_KEY = "identity-framework-signing-key-at-least-32-bytes"
_BASE_PATH = "/openapi/v1/bots/{bot_id}/identity"
_FILE_TYPE = "RULES"
_PATH_PARAMS = {"bot_id": _BOT_ID}
_FILE_PATH_PARAMS = {**_PATH_PARAMS, "file_type": _FILE_TYPE}

#: The whitelisted types the service probes, as the router re-suffixes them.
_PRESENCE = (("RULES.md", True), ("SOUL.md", False))


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
                    "subject": {"id": _OWNER, "username": "identity@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}
_FORBIDDEN_QUERY = {"user_id": "another-user"}
_WRITE_BODY = {"content": "# Rules\n- Never contact customers directly.\n"}


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_happy_services(world) -> None:
    _seed_verifier(world)
    # Every operation resolves the bot's runtime engine through the real
    # ``BotRepository``, so each needs a Bot row to exist.
    make_bot(world, bot_id=_BOT_ID, owner_id=_OWNER)

    async def list_bot_files(_self, *_args, **_kwargs):
        return list(_PRESENCE)

    async def get_bot_file(_self, *args, **_kwargs):
        # (entity_type, entity_id, bot_id, file_type, operator_id)
        file_type = args[3]
        return SimpleNamespace(
            content="# Rules\n",
            file_path=f"{IDENTITY_NS}/{file_type}",
        )

    async def update_bot_file(_self, *args, **_kwargs):
        file_type = args[3]
        return SimpleNamespace(file_path=f"{IDENTITY_NS}/{file_type}")

    bind_overrides(
        world,
        IdentityService,
        {
            "list_bot_files": list_bot_files,
            "get_bot_file": get_bot_file,
            "update_bot_file": update_bot_file,
        },
    )


#: ``(method, path, input, status, body)`` — the body is asserted as a subset,
#: so a case cannot go green on a 200 that carries the wrong payload.
_HAPPY_CASES = (
    (
        "GET",
        _BASE_PATH,
        CaseInput(path_params=_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS),
        200,
        {
            "data": {
                "bot_id": _BOT_ID,
                "files": [
                    {"type": "RULES", "exists": True, "file_path": "identity/RULES.md"}
                ],
            }
        },
    ),
    (
        "GET",
        f"{_BASE_PATH}/{{file_type}}",
        CaseInput(
            path_params=_FILE_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS
        ),
        200,
        {
            "data": {
                "type": _FILE_TYPE,
                "bot_id": _BOT_ID,
                "content": "# Rules\n",
                "file_path": "identity/RULES.md",
            }
        },
    ),
    (
        "PUT",
        f"{_BASE_PATH}/{{file_type}}",
        CaseInput(
            path_params=_FILE_PATH_PARAMS,
            query_params=_QUERY,
            headers=_HEADERS,
            json_body=_WRITE_BODY,
        ),
        200,
        # A reference only — the write does not echo the content back.
        {"data": {"type": _FILE_TYPE, "bot_id": _BOT_ID, "file_path": "identity/RULES.md"}},
    ),
)


for _method, _path, _input, _status, _body in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        input=_input,
        seed=_seed_happy_services,
        expect=ExpectSuccess(status=_status, json_contains=_body),
    )(lambda: None)


# The refusal every user-scoped operation on this surface owes: ``user_id``
# names someone other than the caller the principal authenticated. It is raised
# by ``require_user_id`` ahead of the handler, so no identity service is seeded
# — reaching one would itself be the bug.
for _method, _path, _input, _status, _body in _HAPPY_CASES:
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
