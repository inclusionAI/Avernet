"""Endpoint-framework coverage for the user-identity read.

The framework owns invocation, so these two cases are what makes
``GET /openapi/v1/org/user`` *covered* rather than merely tested: the happy path
— a verified user reads the identity the gateway signed — and the refusal the
operation most needs pinned, because its whole answer is an identity: no
signed principal, no answer.

``test_org_user_identity.py`` covers the same outcomes plus the app-only refusal
and the tenant rules in the adapter's own suite. The duplication is the
coverage gate's design: it reads this registry, not that file.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PATH = "/openapi/v1/org/user"
_CALLER = "caller-identity-user"
_KEY = "caller-identity-framework-signing-key-32b"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    """A user-only identity set — the caller shape this endpoint exists for."""
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
                    "subject": {
                        "id": _CALLER,
                        "username": "caller@example.test",
                        "display_name": "Caller",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


def _boot_verifier(_world) -> None:
    """Install the shared key both cases are judged against.

    The refusal case needs it as much as the happy one: without a booted
    verifier the 401 could come from an unconfigured seam rather than from the
    missing header, and the case would pass for the wrong reason.
    """
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="returns_the_verified_identity",
    input=CaseInput(headers={PRINCIPAL_HEADER: _principal()}),
    seed=_boot_verifier,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "message": "OK",
            "data": {
                "user_id": _CALLER,
                "username": "caller@example.test",
                "display_name": "Caller",
            },
        },
    ),
)
def caller_identity_ok():
    """Body intentionally empty — the framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="unauthenticated",
    input=CaseInput(),
    seed=_boot_verifier,
    expect=ExpectError(
        status=401,
        json_contains={"code": 401000, "message": "Unauthorized", "data": None},
    ),
)
def caller_identity_requires_a_principal():
    """No ``X-Avernet-Principal`` header — the surface's uniform refusal."""
