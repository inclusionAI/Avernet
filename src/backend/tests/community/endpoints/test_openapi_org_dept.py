"""Endpoint-framework coverage for the department directory search.

Mirrors ``test_openapi_org_user_identity.py`` for ``GET /openapi/v1/org/dept``:
the two cases that make the route *covered* rather than merely tested — the
happy path and the one failure its contract most needs pinned, because the
answer distinguishes "directory down" (5xx) from "no matches" (200 + ``[]``).

The framework owns invocation, so the declared ``(method, path)`` is what runs;
``test_staff_dept.py`` covers the same outcomes via a hand-built app + DI double
in the adapter's own suite, but the coverage gate reads *this* registry, not
that file (gate design per ``tests/community/framework/README.md``).
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.adapters.http.openapi_v1.errors import DeptLookupError
from agentclaw.community.plugin_api.staff_dept import StaffDeptPlugin
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)
from tests.community.framework.di_seams import bind_failing_method

_PATH = "/openapi/v1/org/dept"
_CALLER = "dept-search-user"
_KEY = "dept-search-framework-signing-key-32b"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    """A user-only identity — the caller shape this surface accepts."""
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
                        "username": "dept@example.test",
                        "display_name": "Dept Searcher",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


def _boot_verifier(_world) -> None:
    """Install the shared key both cases are judged against.

    The failure case needs it as much as the happy one: without a booted
    verifier the 502-vs-401 distinction would be confounded by an unconfigured
    auth seam rather than read off the directory-down branch.
    """
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="returns_empty_list_when_no_directory",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        query_params={"keyword": "platform"},
    ),
    seed=_boot_verifier,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "message": "OK",
            "data": [],
        },
    ),
)
def dept_search_empty():
    """Body intentionally empty — the framework owns invocation.

    The local/community ``StaffDeptPlugin`` is a no-directory noop, so ``[]`` is
    the honest "no matches / no directory" answer — a 200, distinct from the 5xx
    the directory-down case below pins.
    """


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="directory_down_surfaces_5xx",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        query_params={"keyword": "platform"},
    ),
    seed=lambda world: (
        _boot_verifier(world),
        bind_failing_method(
            world,
            StaffDeptPlugin,
            "search_depts",
            DeptLookupError("master-data service unreachable"),
        ),
    ),
    expect=ExpectError(
        status=502,
        json_contains={
            "code": 502000,
            "message": "Department directory unavailable",
            "data": None,
        },
    ),
)
def dept_search_directory_down():
    """The directory-down branch a real infrastructure fault reaches.

    ``bind_failing_method`` makes the wired ``StaffDeptPlugin.search_depts``
    raise the production error (``DeptLookupError``) on the per-test injector —
    the sanctioned DI seam for a failure no input can talk into — so the case
    documents the ``@envelope_errors`` mapping it pins rather than substitute a
    mock that would lie about coverage.
    """
