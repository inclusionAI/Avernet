"""Framework coverage for ``GET /api/v1/org/user?user_id=`` — the decoupled
directory-identity read.

Two cases satisfy the coverage gate (happy + error): a verified caller names a
``user_id`` and gets the directory entry (identity null with no reader wired —
the framework app binds no ``StaffDeptPlugin`` for this surface), and no signed
principal is answered the surface's ``401``.

Adapter-level behaviour — lookup with a wired reader, ``DeptLookupError`` →
``5xx``, app-only admittance — lives in the adapter suite at
``adapters/http/org/test_org_user.py``. This file exists because the coverage
gate reads the ``@endpoint_test`` registry, not that file.
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

_PATH = "/api/v1/org/user"
_CALLER = "org-user-framework-caller"
_KEY = "org-user-framework-signing-key-32b"


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
                        "username": f"{_CALLER}@example.test",
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
    verifier the 401 could come from an unconfigured seam rather than the
    missing header, and the case would pass for the wrong reason.
    """
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="directory_lookup_by_user_id",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        query_params={"user_id": _CALLER},
    ),
    seed=_boot_verifier,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "message": "OK",
            "data": {"user_id": _CALLER, "username": None},
        },
    ),
)
def org_user_directory_lookup_ok():
    """Body intentionally empty — the framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="unauthenticated",
    input=CaseInput(),
    seed=_boot_verifier,
    # A decoupled ``/api`` route reaching the verifier directly is answered with
    # the internal ``401`` ``{"detail": ...}`` shape (the app-level
    # ``_principal_error_handler``), not the public Envelope.
    expect=ExpectError(
        status=401,
        json_contains={"detail": "Unauthorized"},
    ),
)
def org_user_requires_a_principal():
    """No ``X-Avernet-Principal`` — the internal-``/api`` uniform 401."""
