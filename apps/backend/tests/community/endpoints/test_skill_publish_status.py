"""Endpoint coverage for the public Skill publish-status query."""
from __future__ import annotations

import time

import jwt

from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

_SIGNING_KEY = "skill-status-endpoint-secret-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _SIGNING_KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _headers() -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "tenant": "skill-status-test",
                    "subject": {"id": "skill-status-user", "username": "skill@example.com"},
                }
            ],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )
    return {"X-Avernet-Principal": token}


def _enable_auth(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


endpoint_test(
    method="GET",
    path="/openapi/v1/bots/skills/{skill_code}/publish/status",
    scenario="happy",
    seed=_enable_auth,
    input=CaseInput(headers=_headers(), path_params={"skill_code": "demo-skill"}),
    expect=ExpectSuccess(status=200),
)(lambda: None)
endpoint_test(
    method="GET",
    path="/openapi/v1/bots/skills/{skill_code}/publish/status",
    scenario="error",
    seed=_enable_auth,
    input=CaseInput(headers={}, path_params={"skill_code": "demo-skill"}),
    expect=ExpectError(status=401),
)(lambda: None)
