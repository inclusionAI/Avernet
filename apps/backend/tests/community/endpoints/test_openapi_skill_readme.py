"""Endpoint coverage for the Skill-ID addressed README operation."""
from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.skill_query_service import SkillQueryServiceProtocol
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


_USER_ID = "skill-readme-user"
_KEY = "skill-readme-framework-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


class _ReadmeService:
    async def get_readme_by_skill(self, *, skill_id: str, actor_id: str) -> str:
        if skill_id == "missing":
            raise LocalSkillNotFoundError()
        assert actor_id == _USER_ID
        return "# Skill README"


def _principal() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {"type": "user", "subject": {"id": _USER_ID, "username": "readme@test"}}
            ],
        },
        _KEY,
        algorithm="HS256",
    )


def _seed(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    world.injector.binder.bind(SkillQueryServiceProtocol, to=_ReadmeService(), scope=None)


def _input(skill_id: str) -> CaseInput:
    return CaseInput(
        path_params={"skill_id": skill_id},
        headers={PRINCIPAL_HEADER: _principal()},
    )


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/skills/{skill_id}/readme",
    scenario="happy",
    input=_input("ok"),
    seed=_seed,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"content": "# Skill README"}},
    ),
)
def skill_readme_happy():
    """A verified user receives the README in the OpenAPI envelope."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/skills/{skill_id}/readme",
    scenario="not_found",
    input=_input("missing"),
    seed=_seed,
    expect=ExpectError(status=404),
)
def skill_readme_not_found():
    """A missing Skill is masked as a 404."""
