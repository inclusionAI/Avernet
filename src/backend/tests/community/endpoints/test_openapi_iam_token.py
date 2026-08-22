"""Endpoint coverage for the OpenAPI IAM-token migration."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_USER_ID = "openapi-iam-user"
_BOT_ID = "openapi-iam-bot"
_IAM_TOKEN = "opaque-iam-token"
_KEY = "openapi-iam-framework-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
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
                        "id": _USER_ID,
                        "username": "openapi-iam@example.test",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {
    PRINCIPAL_HEADER: _principal(),
    "cookie": f"IAM_TOKEN={_IAM_TOKEN}",
}
_QUERY = {"user_id": _USER_ID}


def _boot_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_non_caller_bot(world) -> None:
    _boot_verifier(world)
    world.get(BotRepository).insert(
        {
            "bot_id": _BOT_ID,
            "bot_name": "OpenAPI IAM Bot",
            "owner_id": _USER_ID,
            "owner_name": _USER_ID,
            "entity_id": _USER_ID,
            "entity_type": "staff",
            "creator_id": _USER_ID,
            "status": "ACTIVE",
            "active_engine": "openclaw",
            "bot_type": "personal",
        }
    )


def _assert_no_store(response, _world) -> None:
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["pragma"] == "no-cache"


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/iam-token",
    scenario="returns_the_cookie_and_skips_caller_for_a_personal_bot",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_non_caller_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "message": "OK",
            "data": {"iam_token": _IAM_TOKEN},
        },
    ),
    extra_assertions=(_assert_no_store,),
)
def get_bot_iam_token_ok():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/iam-token",
    scenario="missing_iam_cookie",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers={PRINCIPAL_HEADER: _principal()},
    ),
    seed=_boot_verifier,
    expect=ExpectError(
        status=401,
        json_contains={
            "code": 401000,
            "message": "IAM credential is unavailable",
            "data": None,
        },
    ),
    extra_assertions=(_assert_no_store,),
)
def get_bot_iam_token_missing_cookie():
    """The framework owns invocation."""
