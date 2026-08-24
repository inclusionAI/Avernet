"""Tests for the independent Bot Logs OpenAPI service."""

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_chat.open_service import OpenBotChatService
from agentclaw.community.core.bot_chat.schemas import ConversationDetail


@pytest.fixture
def repository():
    return MagicMock()


@pytest.fixture
def service(repository):
    return OpenBotChatService(repository=repository)


@pytest.mark.asyncio
async def test_open_group_query_uses_independent_repository(service, repository):
    await service.list_open_sessions(group_id=" group_fixture ")

    kwargs = repository.list_scope_traces.call_args.kwargs
    assert kwargs["group_id"] == "group_fixture"
    assert kwargs["session_key"] is None
    assert kwargs["from_ms"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"biz_scene": "scene_fixture"},
        {"biz_task_id": "task_fixture"},
        {"session_key": "session_fixture", "group_id": "group_fixture"},
    ],
)
async def test_open_query_rejects_invalid_mode_combinations(service, params):
    with pytest.raises(ValueError):
        await service.list_open_sessions(**params)


@pytest.mark.asyncio
async def test_open_detail_uses_independent_repository(service, repository):
    detail = MagicMock(spec=ConversationDetail)
    repository.get_trace_detail.return_value = detail

    result = await service.get_open_session(" trace_fixture ")

    assert result is detail
    repository.get_trace_detail.assert_called_once_with("trace_fixture")


@pytest.mark.asyncio
async def test_open_user_bot_query_uses_independent_repository(service, repository):
    result = MagicMock()
    repository.list_user_bot_traces.return_value = result

    actual = await service.list_open_user_bot_traces(
        " user_fixture ", " bot_fixture ", page=2, limit=200
    )

    assert actual is result
    kwargs = repository.list_user_bot_traces.call_args.kwargs
    assert kwargs["user_id"] == "user_fixture"
    assert kwargs["bot_id"] == "bot_fixture"
    assert kwargs["page"] == 2
    assert kwargs["limit"] == 100
    assert kwargs["from_ms"] < kwargs["to_ms"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_id", "bot_id"),
    [("", "bot_fixture"), ("user_fixture", "  ")],
)
async def test_open_user_bot_query_rejects_blank_identifiers(
    service, user_id, bot_id
):
    with pytest.raises(ValueError):
        await service.list_open_user_bot_traces(user_id, bot_id)
