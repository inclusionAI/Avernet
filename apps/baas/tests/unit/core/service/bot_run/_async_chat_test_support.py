"""async chat test support; focused regression tests and fixtures."""

from unittest.mock import AsyncMock, patch

import pytest

_TEST_SESSION_KEY = "test-session-key"


def _setup_session_state(client, session_key=_TEST_SESSION_KEY):
    """Helper: register a _SessionState for a given session_key."""
    from secbaas.community.core.service.bot_run._async_chat_client import SessionState

    return client._sessions.setdefault(session_key, SessionState())


@pytest.fixture
def mock_bot_ws():
    """Create a mock BotWebSocketClient."""
    with patch(
        "secbaas.community.core.service.bot_run._async_chat_client_ws.BotWebSocketClient"
    ) as mock:
        yield mock


@pytest.fixture
def mock_bot_ws_instance(mock_bot_ws):
    """Create a mock BotWebSocketClient instance with async methods."""
    instance = mock_bot_ws.return_value
    # 所有方法改为 async
    instance.connect = AsyncMock(
        return_value={
            "server": {"host": "testsrv", "port": 8080},
            "features": {"chat": True},
        }
    )
    instance.close = AsyncMock()
    instance.chat_send = AsyncMock(return_value={"ok": True})
    instance.chat_inject = AsyncMock(return_value={"ok": True})
    instance.connected = True
    return instance


@pytest.fixture
def hello_response():
    """Standard handshake response."""
    return {
        "server": {"host": "testsrv", "port": 8080},
        "features": {"chat": True},
    }
