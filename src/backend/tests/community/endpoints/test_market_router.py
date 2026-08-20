"""Endpoint coverage for the public Skill and MCP marketplace queries."""
from __future__ import annotations

import time

import jwt

from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

_USER_ID = "market-endpoint-user"
_SIGNING_KEY = "market-endpoint-secret-key-at-least-32-bytes"


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
                    "tenant": "market-endpoint-test",
                    "subject": {"id": _USER_ID, "username": "market@example.com"},
                }
            ],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )
    return {"X-Avernet-Principal": token}


def _enable_auth(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _input(body: dict) -> CaseInput:
    return CaseInput(
        headers=_headers(),
        json_body=body,
    )


for _path, _body in (
    ("/openapi/v1/bots/market/skills", {"keyword": "", "page_num": 1, "page_size": 20}),
    ("/openapi/v1/bots/market/mcp-servers", {"keyword": "", "page_num": 1, "page_size": 20}),
    ("/openapi/v1/bots/market/skill-center/skills", {"keyword": "", "pageNum": 1, "pageSize": 20}),
):
    endpoint_test(
        method="POST",
        path=_path,
        scenario="happy",
        seed=_enable_auth,
        input=_input(_body),
        expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": {"total": 0}}),
    )(lambda: None)
    endpoint_test(
        method="POST",
        path=_path,
        scenario="invalid_request",
        seed=_enable_auth,
        input=_input({"page_num": "not-an-integer", "pageNum": "not-an-integer"}),
        expect=ExpectError(status=422),
    )(lambda: None)
