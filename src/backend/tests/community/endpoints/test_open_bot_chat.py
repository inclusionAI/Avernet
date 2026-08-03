"""Endpoint coverage for owner-independent bot-chat reads."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import patch

import jwt

from agentclaw.community.core.bot_chat.models import BcsGroupSession
from agentclaw.community.core.bot_chat.repository import BotChatDbRepository
from agentclaw.community.core.gateway_principal import PrincipalVerifierConfig
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_SESSION_PATH = "/openapi/v1/bots/logs/sessions/{session_key}/traces"
_TASK_PATH = "/openapi/v1/bots/logs/tasks/{biz_scene}/{biz_task_id}/traces"
_GROUP_PATH = "/openapi/v1/bots/logs/groups/{group_id}/traces"
_TRACES_PATH = "/openapi/v1/bots/logs/traces"
_DETAIL_PATH = "/openapi/v1/bots/logs/traces/{trace_id}"
_INTERNAL_LIST_PATH = "/api/v1/open/bot-chats"
_INTERNAL_DETAIL_PATH = "/api/v1/open/bot-chats/{trace_id}"
_INTERNAL_USER_BOT_PATH = (
    "/api/v1/open/bot-chats/users/{user_id}/bots/{bot_id}/traces"
)
_TRACE_ID = "trace_open_endpoint_fixture"
_SESSION_ID = "session_open_endpoint_fixture"
_BCS_SESSION_ID = "group_open_endpoint_fixture:abcdef12"
_SESSION_KEY = f"agent:main:bcs:group:{_BCS_SESSION_ID}"
_SIGNING_KEY = "endpoint-test-shared-secret-at-least-32-bytes"


def _principal_headers() -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 60,
            "principals": [
                {
                    "type": "user",
                    "tenant": "default",
                    "subject": {
                        "id": "endpoint-user",
                        "username": "endpoint-user@example.com",
                    },
                }
            ],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )
    return {"X-Avernet-Principal": token}


def _enable_public_auth(_world) -> None:
    patch(
        "agentclaw.community.adapters.http.openapi_v1.dependencies."
        "get_principal_verifier_config",
        return_value=PrincipalVerifierConfig(
            signing_key=_SIGNING_KEY,
            audience="backend",
            issuer="gateway",
        ),
    ).start()


def _seed_trace(world) -> None:
    _enable_public_auth(world)
    db = world.get(DatabasePlugin)
    BotChatDbRepository(db).upsert_ocb_trace(
        {
            "trace_id": _TRACE_ID,
            "session_id": _SESSION_ID,
            "session_key": _SESSION_KEY,
            "user_id": "user_open_endpoint_fixture",
            "bot_id": "bot_open_endpoint_fixture",
            "biz_scene": "scene_open_endpoint_fixture",
            "biz_task_id": "task_open_endpoint_fixture",
            "name": "Open endpoint fixture",
            "input": "synthetic input",
            "output": "synthetic output",
            "start_time_ms": 1_000,
            "status": "SUCCESS",
            "usage": {},
        }
    )


def _seed_group_trace(world) -> None:
    _seed_trace(world)
    with world.get(DatabasePlugin).orm_session() as session:
        session.add(
            BcsGroupSession(
                session_id=_BCS_SESSION_ID,
                group_id="group_open_endpoint_fixture",
                session_kind="chat",
                env=get_current_env(),
            )
        )


def _seed_user_bot_traces(world) -> None:
    _enable_public_auth(world)
    repo = BotChatDbRepository(world.get(DatabasePlugin))
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for trace_id, user_id, bot_id in (
        ("trace_user_bot_match", "user_pair_fixture", "bot_pair_fixture"),
        ("trace_other_user", "other_user_fixture", "bot_pair_fixture"),
        ("trace_other_bot", "user_pair_fixture", "other_bot_fixture"),
    ):
        repo.upsert_ocb_trace(
            {
                "trace_id": trace_id,
                "session_id": f"session_{trace_id}",
                "session_key": f"agent:main:{trace_id}",
                "user_id": user_id,
                "bot_id": bot_id,
                "name": "User Bot endpoint fixture",
                "input": "synthetic input",
                "output": "synthetic output",
                "start_time_ms": now_ms,
                "status": "SUCCESS",
                "usage": {},
            }
        )
    with world.get(DatabasePlugin).orm_session() as session:
        session.add(
            BcsGroupSession(
                session_id="trace_user_bot_match",
                group_id="group_user_bot_fixture",
                session_kind="chat",
                env=get_current_env(),
            )
        )


@endpoint_test(
    method="GET",
    path=_SESSION_PATH,
    scenario="happy",
    seed=_seed_trace,
    input=CaseInput(
        path_params={"session_key": _SESSION_KEY},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "total": 1,
                "sessions": [{"id": _TRACE_ID, "session_key": _SESSION_KEY}],
            },
        },
    ),
)
def open_bot_chat_session_list_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_SESSION_PATH,
    scenario="invalid_limit",
    seed=_enable_public_auth,
    input=CaseInput(
        path_params={"session_key": _SESSION_KEY},
        query_params={"limit": 101},
        headers=_principal_headers(),
    ),
    expect=ExpectError(status=422),
)
def open_bot_chat_session_list_invalid_limit():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_TASK_PATH,
    scenario="happy",
    seed=_seed_trace,
    input=CaseInput(
        path_params={
            "biz_scene": "scene_open_endpoint_fixture",
            "biz_task_id": "task_open_endpoint_fixture",
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
def open_bot_chat_task_list_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_TASK_PATH,
    scenario="invalid_limit",
    seed=_enable_public_auth,
    input=CaseInput(
        path_params={"biz_scene": "scene", "biz_task_id": "task"},
        query_params={"limit": 101},
        headers=_principal_headers(),
    ),
    expect=ExpectError(status=422),
)
def open_bot_chat_task_list_invalid_limit():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_GROUP_PATH,
    scenario="happy",
    seed=_seed_group_trace,
    input=CaseInput(
        path_params={"group_id": "group_open_endpoint_fixture"},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "total": 1,
                "sessions": [{"id": _TRACE_ID}],
            },
        },
    ),
)
def open_bot_chat_group_list_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_GROUP_PATH,
    scenario="invalid_limit",
    seed=_enable_public_auth,
    input=CaseInput(
        path_params={"group_id": "group_fixture"},
        query_params={"limit": 101},
        headers=_principal_headers(),
    ),
    expect=ExpectError(status=422),
)
def open_bot_chat_group_list_invalid_limit():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_TRACES_PATH,
    scenario="happy",
    seed=_seed_user_bot_traces,
    input=CaseInput(
        query_params={
            "user_id": "user_pair_fixture",
            "bot_id": "bot_pair_fixture",
        },
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "total": 1,
                "sessions": [
                    {
                        "id": "trace_user_bot_match",
                        "group_id": "group_user_bot_fixture",
                        "session_kind": "chat",
                    }
                ],
            },
        },
    ),
)
def open_bot_chat_user_bot_list_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_TRACES_PATH,
    scenario="missing_bot_id",
    seed=_enable_public_auth,
    input=CaseInput(
        query_params={"user_id": "user_pair_fixture"},
        headers=_principal_headers(),
    ),
    expect=ExpectError(status=422),
)
def open_bot_chat_user_bot_list_missing_bot_id():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_DETAIL_PATH,
    scenario="happy",
    seed=_seed_trace,
    input=CaseInput(
        path_params={"trace_id": _TRACE_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"id": _TRACE_ID}},
    ),
)
def open_bot_chat_detail_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_DETAIL_PATH,
    scenario="not_found",
    seed=_enable_public_auth,
    input=CaseInput(
        path_params={"trace_id": "missing_trace_fixture"},
        headers=_principal_headers(),
    ),
    expect=ExpectError(
        status=404,
        json_contains={"code": 404000, "message": "Not found"},
    ),
)
def open_bot_chat_detail_not_found():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_INTERNAL_LIST_PATH,
    scenario="happy",
    seed=_seed_trace,
    input=CaseInput(query_params={"session_key": _SESSION_KEY}),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"total": 1, "sessions": [{"id": _TRACE_ID}]},
        },
    ),
)
def internal_open_bot_chat_list_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_INTERNAL_LIST_PATH,
    scenario="missing_query_mode",
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 4000},
    ),
)
def internal_open_bot_chat_list_missing_query_mode():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_INTERNAL_DETAIL_PATH,
    scenario="happy",
    seed=_seed_trace,
    input=CaseInput(path_params={"trace_id": _TRACE_ID}),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"id": _TRACE_ID}},
    ),
)
def internal_open_bot_chat_detail_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_INTERNAL_DETAIL_PATH,
    scenario="not_found",
    input=CaseInput(path_params={"trace_id": "missing_trace_fixture"}),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 4004},
    ),
)
def internal_open_bot_chat_detail_not_found():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_INTERNAL_USER_BOT_PATH,
    scenario="happy",
    seed=_seed_user_bot_traces,
    input=CaseInput(
        path_params={
            "user_id": "user_pair_fixture",
            "bot_id": "bot_pair_fixture",
        }
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "total": 1,
                "sessions": [
                    {
                        "id": "trace_user_bot_match",
                        "group_id": "group_user_bot_fixture",
                        "session_kind": "chat",
                    }
                ],
            },
        },
    ),
)
def internal_open_bot_chat_user_bot_list_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_INTERNAL_USER_BOT_PATH,
    scenario="invalid_limit",
    input=CaseInput(
        path_params={
            "user_id": "user_pair_fixture",
            "bot_id": "bot_pair_fixture",
        },
        query_params={"limit": 101},
    ),
    expect=ExpectError(status=422),
)
def internal_open_bot_chat_user_bot_list_invalid_limit():
    """The framework owns invocation."""
