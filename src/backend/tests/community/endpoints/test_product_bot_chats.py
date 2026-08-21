"""Endpoint coverage for product-compatible Bot Chat reads."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.core.repository.implementations.chat.db import (
    BotChatDbRepository,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_LIST_PATH = "/openapi/v1/bots/{bot_id}/chats"
_DETAIL_PATH = "/openapi/v1/bots/{bot_id}/chats/{trace_id}"
_BOT_ID = "default"
_TRACE_ID = "trace_product_chat_endpoint"
_USER_ID = "product-chat-user"
_SIGNING_KEY = "product-chat-endpoint-secret-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _SIGNING_KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal_headers() -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 60 * 60,
            "principals": [
                {
                    "type": "user",
                    "tenant": "product-chat-endpoint-test",
                    "subject": {
                        "id": _USER_ID,
                        "username": "product-chat-user@example.com",
                    },
                }
            ],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )
    return {"X-Avernet-Principal": token}


def _enable_public_auth(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_trace(world) -> None:
    _enable_public_auth(world)
    BotChatDbRepository(world.get(DatabasePlugin)).upsert_ocb_trace(
        {
            "trace_id": _TRACE_ID,
            "session_id": "session_product_chat_endpoint",
            "session_key": "agent:main:session:product-chat:user:product-chat-user",
            "user_id": _USER_ID,
            "bot_id": _BOT_ID,
            "name": "Product chat endpoint fixture",
            "input": "synthetic input",
            "output": "synthetic output",
            "start_time_ms": 1_000,
            "status": "SUCCESS",
            "usage": {},
        }
    )


@endpoint_test(
    method="GET",
    path=_LIST_PATH,
    scenario="happy",
    seed=_seed_trace,
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={
            "user_id": _USER_ID,
            "trace_id": _TRACE_ID,
            "time_scope": "all",
            "log_source": "db",
        },
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"total": 1, "sessions": [{"id": _TRACE_ID}]},
        },
    ),
)
def product_bot_chats_list_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_LIST_PATH,
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={"user_id": "another-user"},
        headers=_principal_headers(),
    ),
    expect=ExpectError(status=403),
)
def product_bot_chats_list_wrong_user():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_DETAIL_PATH,
    scenario="happy",
    seed=_seed_trace,
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "trace_id": _TRACE_ID},
        query_params={"user_id": _USER_ID, "log_source": "db"},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"id": _TRACE_ID, "bot_id": _BOT_ID}},
    ),
)
def product_bot_chat_detail_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_DETAIL_PATH,
    scenario="not_found",
    seed=_enable_public_auth,
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "trace_id": "missing-trace"},
        query_params={"user_id": _USER_ID, "log_source": "db"},
        headers=_principal_headers(),
    ),
    expect=ExpectError(status=404),
)
def product_bot_chat_detail_not_found():
    """The framework owns invocation."""
