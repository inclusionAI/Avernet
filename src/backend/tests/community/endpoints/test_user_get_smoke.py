"""Smoke test for the per-endpoint DI framework.

Targets ``GET /api/v1/user/{user_type}/{user_id}``
(``api/access/router.py:54``). Two cases exercise the full surface:

- **ok**: seeds a user via the ``make_staff_user`` factory, then the
  framework issues the GET. ExpectSuccess pins the envelope shape
  (``success=True``, ``data.userId == "u_smoke"``).
- **not_found**: no seed; the handler catches ``UserNotFoundError``
  and returns ``ApiResponse(success=False, error_code=404)`` at HTTP
  200 — the project's error-envelope convention. ExpectError pins
  that shape via json_contains.

Together these prove the path: decorator → registry → auto-discovery
conftest → parametrized runner → fresh per-test injector + in-memory
DB → factory seed → real handler resolves UserService → response →
expectation check.

The function bodies are intentionally empty: the framework owns
invocation, and the declaration above is the entire case. Putting
logic in the body would be ignored.
"""
from __future__ import annotations

from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


@endpoint_test(
    method="GET",
    path="/api/v1/user/{user_type}/{user_id}",
    scenario="ok",
    input=CaseInput(path_params={"user_type": "staff", "user_id": "u_smoke"}),
    seed=lambda world: make_staff_user(world, user_id="u_smoke"),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "error_code": 200,
            "data": {"userId": "u_smoke", "userType": "staff"},
        },
    ),
)
def get_user_ok():
    """Declarative case — body intentionally empty."""


@endpoint_test(
    method="GET",
    path="/api/v1/user/{user_type}/{user_id}",
    scenario="not_found",
    input=CaseInput(path_params={"user_type": "staff", "user_id": "does_not_exist"}),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def get_user_not_found():
    """Declarative case — body intentionally empty."""
