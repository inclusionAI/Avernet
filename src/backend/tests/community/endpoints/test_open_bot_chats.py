"""Endpoint coverage for the read-only open bot-chat APIs."""
from __future__ import annotations

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
_TRACE_ID = "trace_open_endpoint_fixture"
_SESSION_KEY = "agent:main:session_open_endpoint_fixture"


def _seed_open_trace(world) -> None:
    db = world.get(DatabasePlugin)
    BotChatDbRepository(db).upsert_ocb_trace(
        {
            "trace_id": _TRACE_ID,
            "session_id": "session_open_endpoint_fixture",
            "session_key": _SESSION_KEY,
            "user_id": "owner_open_endpoint_fixture",
            "bot_id": "bot_open_endpoint_fixture",
            "name": "Synthetic open endpoint trace",
            "input": "synthetic input",
            "output": "synthetic output",
            "start_time_ms": 1_000,
            "usage": {},
        }
    )


@endpoint_test(
    method="GET",
    path=_LIST_PATH,
    scenario="session_lookup",
    seed=_seed_open_trace,
    input=CaseInput(query_params={"session_key": _SESSION_KEY}),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "sessions": [{"id": _TRACE_ID, "session_key": _SESSION_KEY}],
                "total": 1,
                "page": 1,
                "limit": 100,
                "has_more": False,
            },
        },
    ),
)
def list_open_bot_chats_by_session():
    """A precise Session lookup returns the seeded cross-owner trace."""


@endpoint_test(
    method="GET",
    path=_LIST_PATH,
    scenario="missing_query_mode",
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 4000},
    ),
)
def list_open_bot_chats_requires_query_mode():
    """The open list cannot be used as an unconditional log browser."""


@endpoint_test(
    method="GET",
    path=_DETAIL_PATH,
    scenario="found",
    seed=_seed_open_trace,
    input=CaseInput(path_params={"trace_id": _TRACE_ID}),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "id": _TRACE_ID,
                "session_key": _SESSION_KEY,
                "input": "synthetic input",
                "output": "synthetic output",
                "observations": [],
            },
        },
    ),
)
def get_open_bot_chat_detail():
    """The detail endpoint opens a Trace without applying owner filtering."""


@endpoint_test(
    method="GET",
    path=_DETAIL_PATH,
    scenario="not_found",
    input=CaseInput(path_params={"trace_id": "missing_open_endpoint_fixture"}),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 4004},
    ),
)
def get_open_bot_chat_detail_not_found():
    """A missing Trace uses the existing not-found error envelope."""
