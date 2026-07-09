"""Tests for ExpertChat API router."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.core.expert_chat.errors import (
    BotNotFoundError,
    BotNotActiveError,
    ConnectionError,
)
from agentclaw.community.core.expert_chat.services.expert_chat_service import ExpertChatService


@pytest.fixture
def mock_expert_chat_service():
    """Mock ExpertChatService for testing."""
    service = MagicMock(spec=ExpertChatService)
    service.add_chat_bot = MagicMock(return_value={
        "id": 1,
        "user_id": "test_user",
        "bot_id": "test_bot",
        "owner_id": "test_owner",
        "status": "ACTIVE"
    })
    service.list_chat_bots = MagicMock(return_value=[])
    service.remove_chat_bot = AsyncMock(return_value=True)
    service.get_chat_session = AsyncMock(return_value={
        "session_key": "session:test",
        "is_new": True,
        "connection": {
            "type": "websocket",
            "url": "http://localhost:8080",
            "engine_type": "openclaw"
        }
    })
    service.delete_chat_session = AsyncMock(return_value=True)
    return service


@pytest.fixture
def client_with_mock(mock_expert_chat_service):
    """Create test client with a minimal FastAPI app that only mounts the expert_chat router.

    Using a dedicated app avoids pulling `agentclaw.community.adapters.http.app`, whose module-level
    startup handlers (skill scan, git sync, local device lifecycle) reach out to external
    URLs and filesystem state — harmless locally but flaky in sandboxed CI.
    """
    from agentclaw.community.adapters.http.expert_chat import router as expert_chat_router
    from agentclaw.community.adapters.http.auth.dependencies import get_current_user
    from agentclaw.community.plugin_api.auth import AuthenticatedIdentity

    app = FastAPI()
    app.include_router(expert_chat_router)

    from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol

    class _TestModule(Module):
        def configure(self, binder):
            binder.bind(ExpertChatService, to=mock_expert_chat_service)
            binder.bind(ExpertChatServiceProtocol, to=mock_expert_chat_service)

    attach_injector(app, Injector([_TestModule()]))

    mock_user = MagicMock(spec=AuthenticatedIdentity)
    mock_user.staffId = "test_user"
    mock_user.operatorName = "Test User"
    mock_user.nickName = "Test User"
    mock_user.tenantId = "test_tenant"
    app.dependency_overrides[get_current_user] = lambda: mock_user

    client = TestClient(app)
    yield client, mock_expert_chat_service


class TestAddChatBot:
    """Tests for POST /api/v1/expert-chats endpoint."""

    def test_add_chat_bot_success(self, client_with_mock):
        """Test successful add chat bot."""
        client, mock_service = client_with_mock

        response = client.post(
            "/api/v1/expert-chats",
            json={"bot_id": "test_bot", "owner_id": "test_owner"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["error_code"] == 0
        mock_service.add_chat_bot.assert_called_once_with(
            user_id="test_user",
            bot_id="test_bot",
            owner_id="test_owner"
        )

    def test_add_chat_bot_not_found(self, client_with_mock):
        """Test add chat bot with bot not found error."""
        client, mock_service = client_with_mock
        mock_service.add_chat_bot.side_effect = BotNotFoundError("Bot不存在")

        response = client.post(
            "/api/v1/expert-chats",
            json={"bot_id": "test_bot", "owner_id": "test_owner"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == 404

    def test_add_chat_bot_not_active(self, client_with_mock):
        """Test add chat bot with bot not active error."""
        client, mock_service = client_with_mock
        mock_service.add_chat_bot.side_effect = BotNotActiveError("Bot未激活")

        response = client.post(
            "/api/v1/expert-chats",
            json={"bot_id": "test_bot", "owner_id": "test_owner"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == 400


class TestListChatBots:
    """Tests for GET /api/v1/expert-chats endpoint."""

    def test_list_chat_bots_success(self, client_with_mock):
        """Test successful list chat bots."""
        client, mock_service = client_with_mock
        mock_service.list_chat_bots.return_value = [
            {
                "bot_id": "bot1",
                "owner_id": "owner1",
                "bot_name": "Bot 1",
                "owner_name": "Owner 1",
                "status": "ACTIVE"
            }
        ]

        response = client.get("/api/v1/expert-chats")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["total"] == 1
        assert len(data["data"]["items"]) == 1


class TestRemoveChatBot:
    """Tests for DELETE /api/v1/expert-chats/{bot_id}/{owner_id} endpoint."""

    def test_remove_chat_bot_success(self, client_with_mock):
        """Test successful remove chat bot."""
        client, mock_service = client_with_mock

        response = client.delete("/api/v1/expert-chats/test_bot/test_owner")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_service.remove_chat_bot.assert_called_once()


class TestGetChatSession:
    """Tests for POST /api/v1/expert-chats/{bot_id}/{owner_id}/session endpoint."""

    def test_get_chat_session_success(self, client_with_mock):
        """Test successful get chat session."""
        client, mock_service = client_with_mock

        response = client.post("/api/v1/expert-chats/test_bot/test_owner/session")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "session_key" in data["data"]
        mock_service.get_chat_session.assert_called_once()

    def test_get_chat_session_connection_error(self, client_with_mock):
        """Test get chat session with connection error."""
        client, mock_service = client_with_mock
        mock_service.get_chat_session.side_effect = ConnectionError(
            "无法连接到Bot服务",
            error_code="5001"
        )

        response = client.post("/api/v1/expert-chats/test_bot/test_owner/session")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == 5001


class TestDeleteChatSession:
    """Tests for DELETE /api/v1/expert-chats/{bot_id}/{owner_id}/session endpoint."""

    def test_delete_chat_session_success(self, client_with_mock):
        """Test successful delete chat session."""
        client, mock_service = client_with_mock

        response = client.delete("/api/v1/expert-chats/test_bot/test_owner/session")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_service.delete_chat_session.assert_called_once()
