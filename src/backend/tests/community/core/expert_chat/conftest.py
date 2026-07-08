"""Fixtures for ExpertChat core tests."""
import pytest
from unittest.mock import MagicMock


@pytest.fixture
def mock_repository():
    """Mock ExpertChatRepository for testing."""
    repo = MagicMock()
    repo.add_chat_bot = MagicMock(return_value={
        "id": 1,
        "user_id": "test_user",
        "bot_id": "test_bot",
        "owner_id": "test_owner",
        "status": "ACTIVE"
    })
    repo.remove_chat_bot = MagicMock(return_value=True)
    repo.list_chat_bots = MagicMock(return_value=[])
    repo.get_session = MagicMock(return_value=None)
    repo.save_session = MagicMock()
    repo.delete_session = MagicMock(return_value=True)
    return repo


@pytest.fixture
def mock_bot_repo():
    """Mock BotRepository for testing."""
    repo = MagicMock()
    repo.get_by_id_and_owner = MagicMock(return_value={
        "bot_id": "test_bot",
        "owner_id": "test_owner",
        "bot_name": "Test Bot",
        "owner_name": "Test Owner",
        "status": "ACTIVE",
        "binding_id": 12345
    })
    return repo


@pytest.fixture
def mock_device_provider():
    """Mock DeviceConnectionProvider for testing."""
    provider = MagicMock()
    provider.get_device_connection_v2 = MagicMock(return_value={
        "type": "websocket",
        "url": "http://localhost:8080",
        "headers": {},
        "use_proxy": False,
        "engine_type": "openclaw"
    })
    return provider
