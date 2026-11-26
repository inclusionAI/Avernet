"""Endpoint-framework coverage for the public work-items snapshot."""

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

_PATH = "/openapi/v1/bots/aicoding/work-items"
_CALLER = "work-items-owner"
_KEY = "work-items-framework-signing-key-32b"


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
                    "subject": {
                        "id": _CALLER,
                        "username": "work-items@example.test",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


def _boot_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="authenticated_empty_snapshot",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        query_params={
            "source": "mine",
            "run_status": "non_idle",
            "limit": "50",
        },
    ),
    seed=_boot_verifier,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "message": "OK",
            "data": {
                "items": [],
                "bots": [],
                "partial": False,
                "scan_limit": 50,
                "filters": {
                    "source": ["mine"],
                    "since_ms": None,
                    "run_status": ["non_idle"],
                },
            },
        },
    ),
)
def authenticated_empty_snapshot():
    """The framework owns invocation."""


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
def unauthenticated_snapshot():
    """No signed human principal, no work-items snapshot."""
