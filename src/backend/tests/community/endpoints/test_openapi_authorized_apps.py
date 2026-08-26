"""Declarative happy/error coverage for the public bot-authorization surface.

The four operations of this group were added to ``coverage_baseline.txt`` on
2026-08-10 with two stated reasons: the case runner authenticates with
``x-user-id`` while these require a gateway-signed principal, and *"two of them
require an App identity on top — which the harness cannot mint at all."*

**Both halves have expired.** The minter arrived in the test tree with
``test_openapi_session_files.py`` — point ``init_principal_verifier_config`` at
a local signing key and sign a token with it — and an App identity is minted the
same way, because the token's ``principals`` claim is a *list* of the gateway's
discriminated union. A ``{"type": "app", "tenant": ..., "app": {...}}`` entry
beside the ``user`` one projects onto ``AppPrincipal`` in
``core/gateway_principal/models.py`` exactly as the ``user`` entry projects onto
``UserPrincipal``, so ``require_user_and_app_principal`` is satisfied by a
token rather than by a stand-in. The App's ``tenant`` is the internal
``teamclaw``: a ``user`` principal asserts no tenant, so the App's is the one
the request is isolated by, and it has to be the tenant the seeded rows live in.

The asymmetry the router's docstring turns on is therefore testable on the
wire, and these cases keep it: granting and the application's own listing
present both identities, while the bot's listing and the withdrawal present
only a user — a withdrawal that needed the application's cooperation would be
no withdrawal at all.

A user-and-app caller is still a **human** request here, not a machine one:
``require_acting_caller`` keys the grant path off a caller naming *no* user, so
an App riding along on a human's identity set is recorded, never adjudicated
against a grant. That is what lets these four be driven to real success.

The grant service is the **real** one over the per-test database rather than a
stand-in: what these cases put on the wire — a withdrawal finding something
live, a listing carrying the row a grant wrote — are outcomes, and substituting
the service would only confirm the handlers call themselves the way they were
written.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_OWNER = "authorized-apps-owner"
_BOT_ID = "authorized-apps-bot"
_KEY = "authorized-apps-framework-signing-key-at-least-32-bytes"
_APP_ID = 4242
_APP_NAME = "coverage-partner"
_BASE_PATH = "/openapi/v1/bots/{bot_id}/authorized-apps"
_APP_VIEW_PATH = "/openapi/v1/bots/authorized"
_PATH_PARAMS = {"bot_id": _BOT_ID}
_APP_PATH_PARAMS = {**_PATH_PARAMS, "app_id": _APP_ID}


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _user_entry() -> dict:
    return {
        "type": "user",
        "subject": {"id": _OWNER, "username": "authorized-apps@example.test"},
    }


def _app_entry() -> dict:
    """The App half of the identity set, as the gateway serializes it.

    ``tenant`` appears twice because the wire shape carries it twice — on the
    principal (what the set asserts, and what the request is isolated by) and
    inside the registered application. Both are the internal tenant, which is
    the one the per-test rows are written under.
    """
    return {
        "type": "app",
        "tenant": DEFAULT_AVERNET_TENANT,
        "app": {
            "app_id": _APP_ID,
            "app_name": _APP_NAME,
            "owners": _OWNER,
            "tenant": DEFAULT_AVERNET_TENANT,
        },
    }


def _principal(*principals: dict) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": list(principals),
        },
        _KEY,
        algorithm="HS256",
    )


#: A caller naming a person, and nothing else — all the bot's listing and the
#: withdrawal require.
_HEADERS = {PRINCIPAL_HEADER: _principal(_user_entry())}
#: A caller naming a person *and* the application acting with them — what
#: granting and the application's own listing require.
_APP_HEADERS = {PRINCIPAL_HEADER: _principal(_user_entry(), _app_entry())}

_QUERY = {"user_id": _OWNER}
_FORBIDDEN_QUERY = {"user_id": "another-user"}


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_bot(world) -> None:
    """The verifier plus the bot every bot-scoped operation resolves."""
    _seed_verifier(world)
    make_bot(world, bot_id=_BOT_ID, owner_id=_OWNER)


def _seed_granted(world) -> None:
    """The same, plus one live delegation for the calling application.

    Written through the real service, so the row the listing reads and the
    withdrawal removes is the one a grant actually produces.
    """
    _seed_bot(world)
    world.get(BotAppGrantServiceProtocol).grant(
        bot_id=_BOT_ID,
        user_id=_OWNER,
        owner_id=_OWNER,
        app_id=_APP_ID,
        app_name=_APP_NAME,
    )


#: The grant as both bot-scoped reads project it.
_AUTHORIZED_APP = {
    "app_id": _APP_ID,
    "app_name": _APP_NAME,
    "bot_id": _BOT_ID,
    "user_id": _OWNER,
    "owner_id": _OWNER,
}

#: ``(method, path, input, seed, status, body)``. The seed differs per
#: operation — a withdrawal needs something live to withdraw, a grant does not
#: — so it travels with the case rather than being shared across the loop.
_HAPPY_CASES = (
    (
        "POST",
        _BASE_PATH,
        CaseInput(
            path_params=_PATH_PARAMS, query_params=_QUERY, headers=_APP_HEADERS
        ),
        _seed_bot,
        201,
        {"data": _AUTHORIZED_APP},
    ),
    (
        "GET",
        _BASE_PATH,
        CaseInput(path_params=_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS),
        _seed_granted,
        200,
        {"data": {"total": 1, "items": [_AUTHORIZED_APP]}},
    ),
    (
        "DELETE",
        f"{_BASE_PATH}/{{app_id}}",
        CaseInput(
            path_params=_APP_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS
        ),
        _seed_granted,
        200,
        {"data": {"deleted": True}},
    ),
    (
        "GET",
        _APP_VIEW_PATH,
        CaseInput(query_params=_QUERY, headers=_APP_HEADERS),
        _seed_granted,
        200,
        {"data": {"total": 1, "items": [{"bot_id": _BOT_ID, "owner_id": _OWNER}]}},
    ),
)


for _method, _path, _input, _seed, _status, _body in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        input=_input,
        seed=_seed,
        expect=ExpectSuccess(status=_status, json_contains=_body),
    )(lambda: None)


# The refusal every user-scoped operation on this surface owes: ``user_id``
# names someone other than the caller the principal authenticated. It is raised
# by ``require_user_id`` ahead of the handler, so nothing but the verifier is
# seeded — reaching a bot or a grant would itself be the bug.
for _method, _path, _input, _seed, _status, _body in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="forbidden_user_scope",
        input=CaseInput(
            path_params=_input.path_params,
            query_params=_FORBIDDEN_QUERY,
            headers=_input.headers,
        ),
        seed=_seed_verifier,
        expect=ExpectError(status=403, json_contains={"data": None}),
    )(lambda: None)
