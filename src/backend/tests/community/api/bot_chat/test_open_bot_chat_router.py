"""HTTP-adapter tests for exact open bot-chat lookups."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.bot_chat.errors import SessionNotFoundError
from agentclaw.community.core.bot_chat.schemas import SessionListResponse


@pytest.mark.asyncio
async def test_open_list_forwards_exact_group_without_owner():
    from agentclaw.community.adapters.http.bot_chat.open_router import (
        list_open_sessions,
    )

    service = MagicMock()
    service.list_open_sessions = AsyncMock(
        return_value=SessionListResponse(
            sessions=[],
            total=0,
            page=1,
            limit=100,
            has_more=False,
        )
    )

    result = await list_open_sessions(
        session_key=None,
        biz_scene=None,
        biz_task_id=None,
        group_id="group_fixture",
        page=1,
        limit=100,
        service=service,
    )

    assert result.success is True
    service.list_open_sessions.assert_awaited_once_with(
        session_key=None,
        biz_scene=None,
        biz_task_id=None,
        group_id="group_fixture",
        page=1,
        limit=100,
    )


@pytest.mark.asyncio
async def test_open_list_maps_invalid_combination_to_existing_error_envelope():
    from agentclaw.community.adapters.http.bot_chat.open_router import (
        list_open_sessions,
    )

    service = MagicMock()
    service.list_open_sessions = AsyncMock(
        side_effect=ValueError("provide exactly one query mode")
    )

    result = await list_open_sessions(
        session_key="session_fixture",
        biz_scene="scene_fixture",
        biz_task_id="task_fixture",
        group_id=None,
        page=1,
        limit=100,
        service=service,
    )

    assert result.success is False
    assert result.error_code == 4000


@pytest.mark.asyncio
async def test_open_detail_maps_missing_trace_to_existing_error_envelope():
    from agentclaw.community.adapters.http.bot_chat.open_router import (
        get_open_session,
    )

    service = MagicMock()
    service.get_open_session = AsyncMock(
        side_effect=SessionNotFoundError("trace not found")
    )

    result = await get_open_session("trace_fixture", service=service)

    assert result.success is False
    assert result.error_code == 4004
