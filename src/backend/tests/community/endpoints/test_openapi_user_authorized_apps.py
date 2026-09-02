"""Declarative happy/error coverage for the account-level authorization surface.

The three operations of ``/openapi/v1/org/user/authorized-apps`` — the user's
own account-level grant, list and withdrawal (``core/user_app_grant``). Driven
exactly as ``test_openapi_authorized_apps.py`` drives the bot-level group:
the verifier is pointed at a local signing key, the token's ``principals``
claim carries a ``user`` entry and, where the operation needs it, an ``app``
entry beside it, and the grant service is the **real** one over the per-test
database.

The asymmetry these cases keep: granting presents both identities (the
application is read off the App principal, never a parameter); listing and
withdrawing present only a user, so a withdrawal never depends on holding the
application's key.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.user_app_grant_service import UserAppGrantServiceProtocol
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_USER = "user-authorized-apps-user"
_KEY = "user-authorized-apps-framework-signing-key-at-least-32-bytes"
_APP_ID = 4343
_APP_NAME = "coverage-account-partner"
_BASE_PATH = "/openapi/v1/org/user/authorized-apps"
_APP_PATH_PARAMS = {"app_id": _APP_ID}


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _user_entry() -> dict:
    return {
        "type": "user",
        "subject": {"id": _USER, "username": "user-authorized-apps@example.test"},
    }


def _app_entry() -> dict:
    return {
        "type": "app",
        "tenant": DEFAULT_AVERNET_TENANT,
        "app": {
            "app_id": _APP_ID,
            "app_name": _APP_NAME,
            "owners": _USER,
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


#: A caller naming a person, and nothing else — all listing and withdrawal need.
_HEADERS = {PRINCIPAL_HEADER: _principal(_user_entry())}
#: A caller naming a person *and* the application acting with them — what
#: granting requires.
_APP_HEADERS = {PRINCIPAL_HEADER: _principal(_user_entry(), _app_entry())}

_QUERY = {"user_id": _USER}
_FORBIDDEN_QUERY = {"user_id": "another-user"}


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_granted(world) -> None:
    """The verifier plus one live account-level grant for the calling app.

    Written through the real service, so the row the listing reads and the
    withdrawal removes is the one a grant actually produces.
    """
    _seed_verifier(world)
    world.get(UserAppGrantServiceProtocol).grant(
        user_id=_USER, app_id=_APP_ID, app_name=_APP_NAME
    )


#: The grant as the reads project it.
_AUTHORIZED_APP = {"app_id": _APP_ID, "app_name": _APP_NAME, "user_id": _USER}

#: ``(method, path, input, seed, status, body)``.
_HAPPY_CASES = (
    (
        "POST",
        _BASE_PATH,
        CaseInput(query_params=_QUERY, headers=_APP_HEADERS),
        _seed_verifier,
        201,
        {"data": _AUTHORIZED_APP},
    ),
    (
        "GET",
        _BASE_PATH,
        CaseInput(query_params=_QUERY, headers=_HEADERS),
        _seed_granted,
        200,
        {"data": {"total": 1, "items": [_AUTHORIZED_APP]}},
    ),
    (
        "DELETE",
        f"{_BASE_PATH}/{{app_id}}",
        CaseInput(path_params=_APP_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS),
        _seed_granted,
        200,
        {"data": {"deleted": True}},
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
# names someone other than the caller the principal authenticated. Raised by
# ``require_user_id`` ahead of the handler, so only the verifier is seeded.
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


# Withdrawing an authorization that is not there answers 404, distinct from a
# successful withdrawal — an integrator reconciling their records needs the two
# to read differently.
endpoint_test(
    method="DELETE",
    path=f"{_BASE_PATH}/{{app_id}}",
    scenario="nothing_to_withdraw",
    input=CaseInput(path_params=_APP_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS),
    seed=_seed_verifier,
    expect=ExpectError(status=404, json_contains={"data": None}),
)(lambda: None)
