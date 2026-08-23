from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.expert_chat.errors import BotNotFoundError, ConnectionError
from agentclaw.community.core.expert_chat.services.expert_chat_owned_session import (
    ExpertChatOwnedSessionMixin,
)


class Harness(ExpertChatOwnedSessionMixin):
    def __init__(self) -> None:
        self.bot = {"bot_id": "b1"}
        self._repo = Mock()
        self._repo.get_owned_session.return_value = {"session_key": "s/1"}
        self._transport = Mock()
        self._transport.invoke = AsyncMock()
        self._get_authorized_chat_bot = Mock(return_value=self.bot)
        self._prepare_chat_connection = AsyncMock(
            return_value=({"base_url": "http://runtime"}, False)
        )
        self.list_chat_sessions = AsyncMock(
            return_value={"items": [{"id": "s/1"}], "total": 1}
        )


@pytest.mark.asyncio
async def test_owned_session_operations_reuse_existing_runtime_transport():
    service = Harness()

    assert (await service.get_owned_chat_session("u", "b", "o", "s/1"))["id"] == "s/1"

    service._transport.invoke.return_value = {
        "data": [{"id": "m1"}],
        "total": 1,
    }
    messages = await service.list_owned_chat_session_messages(
        "u", "b", "o", "s/1", limit=20, offset=3
    )
    assert messages == {"items": [{"id": "m1"}], "total": 1}

    service._transport.invoke.return_value = {"data": {"id": "s/1", "title": "T"}}
    updated = await service.update_owned_chat_session(
        "u", "b", "o", "s/1", {"title": "T"}
    )
    assert updated["title"] == "T"

    assert await service.clear_owned_chat_session_messages("u", "b", "o", "s/1")
    assert await service.set_owned_chat_session_favorite(
        "u", "b", "o", "s/1", True
    )
    assert await service.set_owned_chat_session_favorite(
        "u", "b", "o", "s/1", False
    )

    calls = service._transport.invoke.await_args_list
    assert calls[0].args[2] == "/api/sessions/s%2F1/messages"
    assert calls[-2].args[1] == "PUT"
    assert calls[-1].args[1] == "DELETE"


def test_owned_session_guard_rejects_a_session_not_owned_by_the_user():
    service = Harness()
    service._repo.get_owned_session.return_value = None

    with pytest.raises(BotNotFoundError):
        service._require_owned_session("u", "b", "o", "other")


@pytest.mark.asyncio
async def test_owned_session_operations_fail_when_runtime_is_not_ready():
    service = Harness()
    service._prepare_chat_connection.return_value = (None, True)

    with pytest.raises(ConnectionError):
        await service.list_owned_chat_session_messages("u", "b", "o", "s/1", 20)
    with pytest.raises(ConnectionError):
        await service.update_owned_chat_session("u", "b", "o", "s/1", {})
    with pytest.raises(ConnectionError):
        await service.clear_owned_chat_session_messages("u", "b", "o", "s/1")
    with pytest.raises(ConnectionError):
        await service.set_owned_chat_session_favorite("u", "b", "o", "s/1", True)


@pytest.mark.asyncio
async def test_empty_or_invalid_runtime_session_payload_is_rejected():
    service = Harness()
    service.list_chat_sessions.return_value = {"items": []}
    with pytest.raises(BotNotFoundError):
        await service.get_owned_chat_session("u", "b", "o", "missing")

    service._transport.invoke.return_value = {"data": None}
    with pytest.raises(BotNotFoundError):
        await service.update_owned_chat_session("u", "b", "o", "s/1", {})
