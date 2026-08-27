"""Endpoint-framework coverage for the ordinary current-user read."""

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

_PATH = "/api/v1/org/user"
_USER_ID = "ordinary-org-user"
_SIGNING_KEY = "ordinary-org-user-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _SIGNING_KEY


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
                    "subject": {
                        "id": _USER_ID,
                        "username": "ordinary-user@example.test",
                        "display_name": "Ordinary User",
                    },
                }
            ],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )


def _boot_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="happy",
    seed=_boot_verifier,
    input=CaseInput(headers={PRINCIPAL_HEADER: _principal()}),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "user_id": _USER_ID,
            "username": "ordinary-user@example.test",
            "display_name": "Ordinary User",
        },
    ),
)
def get_org_user_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="unauthenticated",
    seed=_boot_verifier,
    input=CaseInput(),
    expect=ExpectError(
        status=401,
        json_contains={"detail": "Unauthorized"},
    ),
)
def get_org_user_unauthenticated():
    """The framework owns invocation."""
