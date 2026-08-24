"""Declarative happy/error coverage for the public Local Skill read surface.

The two read operations — the bot's skill listing and one skill's metadata —
were carried on ``coverage_baseline.txt`` for the reason every ``/openapi/v1``
line there blames: the case runner authenticates with ``x-user-id``,
``require_principal`` accepts only a gateway-signed token, and the harness had
no minter, so a case could assert nothing but a 401.

``test_openapi_session_files.py`` retired that reason by minting a principal in
the test tree — ``init_principal_verifier_config`` pointed at a local signing
key, and a token signed with it handed to the runner. This file does the same,
following ``test_openapi_routines.py`` and ``test_openapi_resources.py``.

Only the two **read** operations live here. The rest of the group already has
wire-level cases of its own (``test_openapi_skill_upload.py``,
``test_openapi_skill_state.py``, ``test_openapi_skill_delete.py``,
``test_openapi_skill_asset.py``), and this file deliberately does not restate
them.

What these cases reach that ``adapters/http/openapi_v1/test_skills_endpoints.py``
cannot is the assembled application: that suite calls ``await handler(...)``
with every dependency passed by keyword, so neither FastAPI's wiring nor the
injector runs. Here the grant dependency, the pagination parameters, the
``owner_id`` resolution and the page envelope are all on the wire.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.bot_skill_asset_service import (
    BotSkillAssetServiceProtocol,
)
from agentclaw.community.api.local_skill_query_service import (
    LocalSkillQueryServiceProtocol,
)
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

_OWNER = "skills-read-owner"
_BOT_ID = "skills-read-bot"
_SKILL_ID = "7"
_KEY = "skills-read-framework-signing-key-at-least-32-bytes"
_BASE_PATH = "/openapi/v1/bots/{bot_id}/skills"
_PATH_PARAMS = {"bot_id": _BOT_ID}
_SKILL_PATH_PARAMS = {**_PATH_PARAMS, "skill_id": _SKILL_ID}


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
                    "subject": {"id": _OWNER, "username": "skills-read@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}
_FORBIDDEN_QUERY = {"user_id": "another-user"}


def _record() -> dict:
    """One skill row exactly as the query/asset services return it.

    ``bolt_id`` is the bot the skill belongs to and ``user_id`` its owner:
    ``_require_addressed_bot`` compares the first against the address and
    ``_require_skills_grant`` binds the grant check to the pair, so both must
    name the bot and owner the request addresses.
    """
    return {
        "id": _SKILL_ID,
        "name": "weather",
        "description": "Forecast",
        "category": "tools",
        "tags": '["daily"]',
        "active": False,
        "gmt_created": "2026-08-04T00:00:00",
        "gmt_modified": "2026-08-04T01:00:00",
        "bolt_id": _BOT_ID,
        "user_id": _OWNER,
    }


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_happy_services(world) -> None:
    _seed_verifier(world)
    make_bot(world, bot_id=_BOT_ID, owner_id=_OWNER)

    def list_bot_skills(_self, **_kwargs):
        return 1, [_record()]

    bind_overrides(
        world,
        LocalSkillQueryServiceProtocol,
        {"list_bot_skills": list_bot_skills},
    )

    def get_skill(_self, **_kwargs):
        return _record()

    bind_overrides(world, BotSkillAssetServiceProtocol, {"get_skill": get_skill})


#: The skill as the router projects the record — what both operations answer
#: with, one inside a page and one on its own.
_SKILL_BODY = {
    "skill_id": _SKILL_ID,
    "name": "weather",
    "description": "Forecast",
    "category": "tools",
    "tags": ["daily"],
    "active": False,
}

#: ``(method, path, input, status, body)`` — the body is asserted as a subset,
#: so a case cannot go green on a 200 carrying the wrong payload.
_HAPPY_CASES = (
    (
        "GET",
        _BASE_PATH,
        CaseInput(path_params=_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS),
        200,
        {"data": {"total": 1, "items": [_SKILL_BODY]}},
    ),
    (
        "GET",
        f"{_BASE_PATH}/{{skill_id}}",
        CaseInput(
            path_params=_SKILL_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS
        ),
        200,
        {"data": _SKILL_BODY},
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
# names someone other than the caller the principal authenticated.
# ``require_user_id`` raises ahead of the handler — and ahead of the grant
# dependency that depends on it — so no skill service is seeded; reaching one
# would itself be the bug.
for _method, _path, _input, _status, _body in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="forbidden_user_scope",
        input=CaseInput(
            path_params=_input.path_params,
            query_params=_FORBIDDEN_QUERY,
            headers=_HEADERS,
        ),
        seed=_seed_verifier,
        expect=ExpectError(status=403, json_contains={"data": None}),
    )(lambda: None)
