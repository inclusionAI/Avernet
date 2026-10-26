"""Endpoint coverage for manual BBS skill-install triggers.

The internal and public routes expose the same one-shot repair operation. These
cases pin the route-level happy results and the domain ``NotFound`` mapping
without invoking the real Bot transport.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.errors import NotFound
from agentclaw.community.core.task.forum.browsing import BbsBrowseLoopRunner
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_failing_method,
    bind_method,
    endpoint_test,
)

_INTERNAL_PATH = "/api/v1/collaboration/tasks/bots/{bot_id}/bbs/skills/install"
_OPENAPI_PATH = "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/skills/install"

_PRINCIPAL_KEY = "public-bbs-skill-install-signing-key"
_PRINCIPAL_CALLER = "public-bbs-owner"
_SUCCESS_RESULT = {
    "run_id": "run_bbs_skill",
    "session_id": "session_bbs_skill",
    "mode": "openclaw",
    "status": "submitted",
    "action": "install_skills",
}


class _PrincipalSecret:
    secret_user = "test"
    secret_value = _PRINCIPAL_KEY


class _PrincipalSecretResolver:
    def get_secret(self, _secret_name: str) -> _PrincipalSecret:
        return _PrincipalSecret()


def _boot_principal_verifier(_world) -> None:
    init_principal_verifier_config(_PrincipalSecretResolver(), "test-key", strict=False)


def _principal_headers() -> dict[str, str]:
    now = int(time.time())
    principal = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {
                        "id": _PRINCIPAL_CALLER,
                        "username": "bbs-skill-install@example.test",
                    },
                }
            ],
        },
        _PRINCIPAL_KEY,
        algorithm="HS256",
    )
    return {PRINCIPAL_HEADER: principal}


def _seed_submitted_runner(world) -> None:
    async def trigger_install_bbs_skills(self, *, bot_id: str):
        assert bot_id == "bot-a"
        return dict(_SUCCESS_RESULT)

    bind_method(
        world,
        BbsBrowseLoopRunner,
        "trigger_install_bbs_skills",
        trigger_install_bbs_skills,
    )


def _seed_missing_subscription(world) -> None:
    bind_failing_method(
        world,
        BbsBrowseLoopRunner,
        "trigger_install_bbs_skills",
        NotFound("subscription not found"),
    )


@endpoint_test(
    method="POST",
    path=_INTERNAL_PATH,
    scenario="submitted",
    input=CaseInput(path_params={"bot_id": "bot-a"}),
    seed=_seed_submitted_runner,
    expect=ExpectSuccess(
        status=200, json_contains={"code": 200000, "data": _SUCCESS_RESULT}
    ),
)
def internal_skill_install_submitted():
    """The internal route returns the submitted Bot run handles."""


@endpoint_test(
    method="POST",
    path=_INTERNAL_PATH,
    scenario="subscription_not_found",
    input=CaseInput(path_params={"bot_id": "bot-a"}),
    seed=_seed_missing_subscription,
    expect=ExpectError(status=404, json_contains={"detail": "subscription not found"}),
)
def internal_skill_install_subscription_not_found():
    """The internal route maps a missing subscription to a 404 detail."""


@endpoint_test(
    method="POST",
    path=_OPENAPI_PATH,
    scenario="authenticated_submitted",
    input=CaseInput(path_params={"bot_id": "bot-a"}, headers=_principal_headers()),
    seed=lambda world: (
        _boot_principal_verifier(world),
        _seed_submitted_runner(world),
    ),
    expect=ExpectSuccess(
        status=200, json_contains={"code": 200000, "data": _SUCCESS_RESULT}
    ),
)
def openapi_skill_install_submitted():
    """A verified caller receives the submitted Bot run handles."""


@endpoint_test(
    method="POST",
    path=_OPENAPI_PATH,
    scenario="authenticated_subscription_not_found",
    input=CaseInput(path_params={"bot_id": "bot-a"}, headers=_principal_headers()),
    seed=lambda world: (
        _boot_principal_verifier(world),
        _seed_missing_subscription(world),
    ),
    expect=ExpectError(
        status=404, json_contains={"code": 404000, "message": "Not Found"}
    ),
)
def openapi_skill_install_subscription_not_found():
    """A verified caller gets the public 404 envelope when no subscription exists."""
