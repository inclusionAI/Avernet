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

from agentclaw.community.api.task.task_config_service import TaskConfigServiceProtocol
from agentclaw.community.utils.env_utils import get_current_env
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
