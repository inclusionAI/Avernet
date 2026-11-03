"""async chat pool support; focused regression tests and fixtures."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_chat_client():
    """Create a mock AsyncChatClient."""
    client = MagicMock()
    client.is_connected = True
    client.is_reconnecting = False
    client.has_active_sessions = False
    client.active_session_count = 0
    client.connect = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_chat_client_class(mock_chat_client):
    """Patch AsyncChatClient to return our mock."""
    with patch(
        "secbaas.community.core.service.bot_run._async_chat_client_pool.AsyncChatClient",
        return_value=mock_chat_client,
    ) as mock_cls:
        yield mock_cls, mock_chat_client


class _FakeChatClient:
    """Fake AsyncChatClient for concurrency tests.

    Tracks creation count and simulates connect latency.
    active_session_count is controllable per-instance.
    """

    _create_count = 0

    @classmethod
    def reset_create_count(cls):
        cls._create_count = 0

    @classmethod
    def get_create_count(cls):
        return cls._create_count

    def __init__(self, **kwargs):
        _FakeChatClient._create_count += 1
        self._active = 0
        self._connected = False
        self.is_reconnecting = False
        self.transport_mode = getattr(kwargs["transport"], "mode", "off")

    @property
    def is_connected(self):
        return self._connected

    @property
    def active_session_count(self):
        return self._active

    @property
    def has_active_sessions(self):
        return self._active > 0

    async def connect(self):
        await asyncio.sleep(0.005)
        self._connected = True

    async def close(self):
        self._connected = False


def _mode_flip_config_service(holder: dict):
    """可翻转的 SystemConfigManageService：每次 get_config 都读 holder 当前值。"""
    service = MagicMock()

    def _get(conf_key):
        resp = MagicMock()
        resp.conf_value = holder.get("value", "off")
        return resp

    service.get_config.side_effect = _get
    return service
