"""Focused tests for the product Bot Chats public adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.bot_chats.router import (
    get_bot_chat,
    list_bot_chats,
)
from agentclaw.community.core.bot_chat.schemas import (
    ConversationDetail,
    SessionListResponse,
)


def _request(path: str) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )
    request.state.trace_id = "request-trace"
    return request


@pytest.mark.asyncio
async def test_list_delegates_the_product_query_without_reinterpreting_it():
    service = SimpleNamespace(
        list_sessions=AsyncMock(
            return_value=SessionListResponse(
                sessions=[], total=0, page=3, limit=17, has_more=False
            )
        )
    )
    start = datetime(2026, 8, 11, tzinfo=timezone.utc)
    end = datetime(2026, 8, 14, tzinfo=timezone.utc)

    result = await list_bot_chats(
        request=_request("/openapi/v1/bots/bot-1/chats"),
        bot_id="bot-1",
        user_id="user-1",
        owner_id="bot-owner",
        trace_id="trace-1",
        session_id="session-1",
        session_key="session-key-1",
        query="needle",
        biz_scene="scene",
        biz_task_id="task",
        group_id="group",
        match_mode="contains",
        include_output_match=True,
        time_scope="default",
        from_date=start,
        to_date=end,
        page=3,
        limit=17,
        log_source="db",
        service=service,
    )

    assert result.code == 200000
    assert result.request_id == "request-trace"
    service.list_sessions.assert_awaited_once_with(
        owner_id="user-1",
        from_date=start,
        to_date=end,
        page=3,
        limit=17,
        bot_id="bot-1",
        trace_id="trace-1",
        session_id="session-1",
        session_key="session-key-1",
        query="needle",
        biz_scene="scene",
        biz_task_id="task",
        group_id="group",
        match_mode="contains",
        include_output_match=True,
        time_scope="default",
        log_source="db",
    )


@pytest.mark.asyncio
async def test_detail_masks_a_trace_from_a_different_path_bot():
    service = SimpleNamespace(
        get_session=AsyncMock(
            return_value=ConversationDetail(
                id="trace-1",
                bot_id="bot-2",
                name="trace",
                timestamp="2026-08-18T00:00:00Z",
            )
        )
    )

    result = await get_bot_chat(
        request=_request("/openapi/v1/bots/bot-1/chats/trace-1"),
        bot_id="bot-1",
        trace_id="trace-1",
        user_id="user-1",
        owner_id=None,
        log_source="db",
        service=service,
    )

    assert result.status_code == 404
    service.get_session.assert_awaited_once_with(
        trace_id="trace-1", owner_id="user-1", log_source="db"
    )
