"""Endpoint-framework coverage for the load-test hello endpoint.

The framework owns invocation, so these two cases are what makes
``GET /openapi/v1/bots/loadtest/hello`` *covered* rather than merely tested:
the happy path against the assembled application, and the refusal that a
synthetic endpoint most needs pinned — no signed principal, no answer.

``test_loadtest_endpoints.py`` covers the same two outcomes plus the socket in
the adapter's own suite. The duplication is the coverage gate's design: it reads
this registry, not that file.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.adapters.http.openapi_v1.loadtest.router import HELLO_WORLD
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PATH = "/openapi/v1/bots/loadtest/hello"
_CALLER = "loadtest-caller"
_KEY = "loadtest-framework-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    """A user-only identity set — this endpoint scopes to nothing else."""
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
                    "subject": {"id": _CALLER, "username": "loadtest@example.test"},
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
    scenario="returns_hello_world",
    input=CaseInput(headers={PRINCIPAL_HEADER: _principal()}),
    seed=_boot_verifier,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "message": "OK",
            "data": {"message": HELLO_WORLD},
        },
    ),
)
def loadtest_hello_ok():
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
def loadtest_hello_requires_a_principal():
    """No ``X-Avernet-Principal`` header — the surface's uniform refusal."""
