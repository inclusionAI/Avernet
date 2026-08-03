"""Endpoint coverage for owner-independent bot-chat reads."""

from __future__ import annotations

from datetime import datetime, timezone

from agentclaw.community.core.bot_chat.repository import BotChatDbRepository
from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_LIST_PATH = "/api/v1/open/bot-chats"
_DETAIL_PATH = "/api/v1/open/bot-chats/{trace_id}"
_USER_BOT_PATH = "/api/v1/open/bot-chats/users/{user_id}/bots/{bot_id}/traces"
_TRACE_ID = "trace_open_endpoint_fixture"
_SESSION_ID = "session_open_endpoint_fixture"
_SESSION_KEY = "agent:main:session:open-endpoint-fixture:user:test"


def _seed_trace(world) -> None:
    db = world.get(DatabasePlugin)
    BotChatDbRepository(db).upsert_ocb_trace(
        {
            "trace_id": _TRACE_ID,
            "session_id": _SESSION_ID,
            "session_key": _SESSION_KEY,
            "user_id": "user_open_endpoint_fixture",
            "bot_id": "bot_open_endpoint_fixture",
            "name": "Open endpoint fixture",
            "input": "synthetic input",
            "output": "synthetic output",
            "start_time_ms": 1_000,
            "status": "SUCCESS",
            "usage": {},
        }
    )


def _seed_user_bot_traces(world) -> None:
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


@endpoint_test(
    method="GET",
    path=_LIST_PATH,
    scenario="happy",
    seed=_seed_trace,
    input=CaseInput(query_params={"session_key": _SESSION_KEY}),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "total": 1,
                "sessions": [{"id": _TRACE_ID, "session_key": _SESSION_KEY}],
            },
        },
    ),
)
def open_bot_chat_list_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_LIST_PATH,
    scenario="missing_query_mode",
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 4000},
    ),
)
def open_bot_chat_list_missing_query_mode():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_DETAIL_PATH,
    scenario="happy",
    seed=_seed_trace,
    input=CaseInput(path_params={"trace_id": _TRACE_ID}),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"id": _TRACE_ID, "session_key": _SESSION_KEY},
        },
    ),
)
def open_bot_chat_detail_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_DETAIL_PATH,
    scenario="not_found",
    input=CaseInput(path_params={"trace_id": "missing_trace_fixture"}),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 4004},
    ),
)
def open_bot_chat_detail_not_found():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_USER_BOT_PATH,
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
                        "user_id": "user_pair_fixture",
                        "bot_id": "bot_pair_fixture",
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
    path=_USER_BOT_PATH,
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
def open_bot_chat_user_bot_list_invalid_limit():
    """The framework owns invocation."""
