"""async chat coverage support."""

from unittest.mock import AsyncMock, patch

import pytest

from secbaas.community.core.service.bot_run._async_chat_client import (
    AsyncChatClient,
    SessionState,
)

_TEST_SESSION_KEY = "test-session-key"


def _setup_session_state(client, session_key=_TEST_SESSION_KEY):
    return client._sessions.setdefault(session_key, SessionState())


@pytest.fixture
def mock_bot_ws():
    with patch(
        "secbaas.community.core.service.bot_run._async_chat_client_ws.BotWebSocketClient"
    ) as mock:
        yield mock


@pytest.fixture
def mock_bot_ws_instance(mock_bot_ws):
    instance = mock_bot_ws.return_value
    instance.connect = AsyncMock(
        return_value={"server": {"host": "srv"}, "features": {}}
    )
    instance.close = AsyncMock()
    instance.chat_send = AsyncMock(return_value={"ok": True})
    instance.chat_inject = AsyncMock(return_value={"ok": True})
    instance.connected = True
    return instance


@pytest.fixture
async def connected_client(mock_bot_ws, mock_bot_ws_instance):
    """A client that has already connected."""
    client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
    await client.connect()
    yield client
    if client._client is not None:
        await client.close()


def _patch_decorator_test(fn):
    """Helper to verify decorator name-setting logic."""
    from secbaas.community.core.service.bot_run._async_chat_events import (
        _with_session_trace,
    )

    return _with_session_trace("_on_event")(fn)
