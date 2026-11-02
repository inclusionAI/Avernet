"""Endpoint coverage for the internal ``/api/v1/collaboration/tasks/config``
routes (spec 2026-11-02): set (upsert) + match (typed, env-scoped) under
``get_current_user`` auth.

Each live route gets a happy + error case so the endpoint coverage gate stays
green. ``env`` is resolved server-side (test profile -> "dev"); seeds call
``get_current_env()`` to stay in lockstep with the handler, never a hardcoded
"dev" literal (a future conftest SERVER_ENV change would otherwise silently
flip the handler's env and turn a present-config match into ``matched: False``).
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.task.task_config_service import TaskConfigServiceProtocol
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_HEADERS = {"x-user-id": "u_task_config_test"}
_SET_PATH = "/api/v1/collaboration/tasks/config/set"
_MATCH_PATH = "/api/v1/collaboration/tasks/config/match"


def _seed_set(world) -> None:
    """Seed a ``set`` config (member ``bot_a``) in the handler's env so the
    match-happy case observes ``matched: True``."""
    svc = world.get(TaskConfigServiceProtocol)
    svc.set_config(
        config_name="skill_set",
        config_type="set",
        config_value=["bot_a"],
        env=get_current_env(),
        operator="seeder",
    )


@endpoint_test(
    method="POST",
    path=_SET_PATH,
    scenario="happy",
    input=CaseInput(
        headers=_HEADERS,
        json_body={"config_name": "ns", "config_type": "set", "config_value": ["a", "b"]},
    ),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def set_happy():
    """Write a set config; service round-trips and returns a config id."""


@endpoint_test(
    method="POST",
    path=_SET_PATH,
    scenario="error",
    input=CaseInput(
        headers=_HEADERS,
        json_body={"config_name": "ns", "config_type": "bogus", "config_value": ["a"]},
    ),
    expect=ExpectError(status=422),
)
def set_error():
    """Invalid config_type is rejected by the pydantic Literal (422)."""


@endpoint_test(
    method="POST",
    path=_SET_PATH,
    scenario="form_mismatch",
    input=CaseInput(
        headers=_HEADERS,
        json_body={"config_name": "fl", "config_type": "bool", "config_value": "true"},
    ),
    expect=ExpectError(
        status=400, json_contains={"message": "Bad request"}
    ),
)
def set_form_mismatch():
    """config_value shape (str) mismatches config_type=bool at the service layer
    (pydantic accepts the str|bool|list[str] union) → mapped domain 400, not 500.
    Pins spec §value-encode: service form mismatches surface as a mapped bad-request
    domain exception via @envelope_errors, not an unmapped 500."""


@endpoint_test(
    method="POST",
    path=_MATCH_PATH,
    scenario="happy",
    seed=_seed_set,
    input=CaseInput(
        headers=_HEADERS,
        json_body={"config_name": "skill_set", "value": "bot_a"},
    ),
    expect=ExpectSuccess(
        status=200, json_contains={"code": 200000, "data": {"matched": True}}
    ),
)
def match_happy():
    """Match a present set member -> matched: True (env = get_current_env())."""


@endpoint_test(
    method="POST",
    path=_MATCH_PATH,
    scenario="error",
    input=CaseInput(headers=_HEADERS, json_body={"config_name": "skill_set"}),
    expect=ExpectError(status=422),
)
def match_error():
    """Missing ``value`` is rejected by the request schema (422)."""


# ── Public openapi surface: POST /openapi/v1/collaboration/tasks/config/match ──
# require_principal (Gateway-signed X-Avernet-Principal): the happy case needs a
# configured principal verifier + a seeded config; the unauthenticated case has
# no principal header → 401 (mirroring the existing /execute unauthenticated
# case in test_openapi_task_endpoints.py — exact same _Secret/_Resolver shape).
_OPENAPI_MATCH_PATH = "/openapi/v1/collaboration/tasks/config/match"
_PUB_KEY = "public-task-framework-signing-key-32b"
_PUB_CALLER = "task-config-caller"


class _PubSecret:
    secret_user = "test"
    secret_value = _PUB_KEY


class _PubResolver:
    def get_secret(self, _secret_name: str) -> _PubSecret:
        return _PubSecret()


def _boot_verifier(_world) -> None:
    init_principal_verifier_config(_PubResolver(), "test-key", strict=False)


def _pub_principal() -> str:
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
                    "subject": {"id": _PUB_CALLER, "username": "task-config@example.test"},
                }
            ],
        },
        _PUB_KEY,
        algorithm="HS256",
    )


def _openapi_headers() -> dict[str, str]:
    return {PRINCIPAL_HEADER: _pub_principal()}


def _seed_set_and_verify(world) -> None:
    """Boot the principal verifier, then seed the config the happy case matches
    (in the handler's env = get_current_env())."""
    _boot_verifier(world)
    svc = world.get(TaskConfigServiceProtocol)
    svc.set_config(
        config_name="skill_set",
        config_type="set",
        config_value=["bot_a"],
        env=get_current_env(),
        operator="seeder",
    )


@endpoint_test(
    method="POST",
    path=_OPENAPI_MATCH_PATH,
    scenario="happy",
    seed=_seed_set_and_verify,
    input=CaseInput(
        headers=_openapi_headers(),
        json_body={"config_name": "skill_set", "value": "bot_a"},
    ),
    expect=ExpectSuccess(
        status=200, json_contains={"code": 200000, "data": {"matched": True}}
    ),
)
def openapi_match_happy():
    """Public match with a signed principal -> matched: True."""


@endpoint_test(
    method="POST",
    path=_OPENAPI_MATCH_PATH,
    scenario="error",
    seed=_boot_verifier,
    input=CaseInput(json_body={"config_name": "skill_set", "value": "bot_a"}),
    expect=ExpectError(status=401, json_contains={"code": 401000, "message": "Unauthorized"}),
)
def openapi_match_unauthenticated():
    """No principal header → 401 (require_principal reject)."""
