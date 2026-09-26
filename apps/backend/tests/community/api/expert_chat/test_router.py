"""Tests for ExpertChat API router."""
import logging

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.core.expert_chat.errors import (
    BotNotFoundError,
    BotNotActiveError,
    BotNotPublishedError,
    ChatPermissionError,
    ConnectionError,
    SessionCreateError,
)
from agentclaw.community.core.expert_chat.services.expert_chat_service import (
    ExpertChatService,
)
from agentclaw.community.core.expert_chat.services.expert_chat_instance_service import (
    ExpertChatInstanceService,
)


@pytest.fixture
def mock_expert_chat_service():
    """Mock ExpertChatService for testing."""
    service = MagicMock(spec=ExpertChatService)
    service.add_chat_bot = MagicMock(
        return_value={
            "id": 1,
            "user_id": "test_user",
            "bot_id": "test_bot",
            "owner_id": "test_owner",
            "status": "ACTIVE",
        }
    )
    service.list_chat_bots = MagicMock(return_value=[])
    service.remove_chat_bot = AsyncMock(return_value=True)
    service.get_chat_session = AsyncMock(
        return_value={
            "session_key": "session:test",
            "is_new": True,
            "connection": {
                "type": "websocket",
                "url": "http://localhost:8080",
                "engine_type": "openclaw",
            },
        }
    )
    service.delete_chat_session = AsyncMock(return_value=True)
    service.list_chat_sessions = AsyncMock(
        return_value={
            "total": 1,
            "items": [
                {
                    "id": "session:test",
                    "title": "Test session",
                    "user_id": "test_user",
                    "agent_id": "test_bot",
                    "model": None,
                    "permission_mode": None,
                    "cwd": None,
                    "gmt_created": "2026-08-10T10:00:00Z",
                    "gmt_modified": "2026-08-10T10:01:00Z",
                    "message_count": 1,
                    "last_message": {"role": "user", "content": "hello"},
                }
            ],
        }
    )
    service.create_chat_session = AsyncMock(
        return_value={
            "session_key": "session:new",
            "is_new": True,
            "connection": {"type": "local"},
        }
    )
    service.connect_chat_session = AsyncMock(
        return_value={
            "session_key": "session:test",
            "is_new": False,
            "connection": {"type": "local"},
        }
    )
    service.delete_owned_chat_session = AsyncMock(return_value=True)
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


@pytest.fixture
def caller_connection_client():
    """Create a focused caller-connection client with a mockable Service API."""
    from agentclaw.community.adapters.http.expert_chat import router as expert_chat_router
    from agentclaw.community.adapters.http.auth.dependencies import get_current_user
    from agentclaw.community.api.expert_chat_instance_service import (
        ExpertChatInstanceServiceProtocol,
    )
    from agentclaw.community.plugin_api.auth import AuthenticatedIdentity

    app = FastAPI()
    app.include_router(expert_chat_router)

    instance_service = MagicMock(spec=ExpertChatInstanceService)
    instance_service.get_authorized_caller_connection = AsyncMock()

    class _TestModule(Module):
        def configure(self, binder):
            binder.bind(
                ExpertChatInstanceServiceProtocol, to=instance_service
            )

    attach_injector(app, Injector([_TestModule()]))

    mock_user = MagicMock(spec=AuthenticatedIdentity)
    mock_user.staffId = "caller_user"
    mock_user.operatorName = "Caller User"
    mock_user.nickName = "Caller User"
    mock_user.tenantId = "test_tenant"
    app.dependency_overrides[get_current_user] = lambda: mock_user

    with TestClient(app) as client:
        yield client, instance_service, mock_user


class TestCallerConnection:
    """Router adaptation and safe boundary logging for caller instances."""

    @staticmethod
    def _post(client):
        return client.post(
            "/api/v1/expert-chats/caller-connection",
            params={
                "bot_id": "caller_bot",
                "owner_id": "caller_owner",
                "user_id": "caller_user",
                "force_upgrade": "true",
            },
        )

    def test_passes_actor_role_and_logs_safe_success(
        self, caller_connection_client, caplog
    ):
        client, instance_service, _ = caller_connection_client
        instance_service.get_authorized_caller_connection.return_value = {
            "instance": {"ext": {"connection": "SENSITIVE_CONNECTION"}},
            "connection": {"token": "SENSITIVE_TOKEN"},
            "need_poll": False,
        }

        with caplog.at_level(logging.INFO), patch(
            "agentclaw.community.adapters.http.expert_chat.router.super_admin",
            return_value=frozenset(),
        ):
            response = self._post(client)

        assert response.status_code == 200
        assert response.json()["success"] is True
        instance_service.get_authorized_caller_connection.assert_awaited_once_with(
            operator_id="caller_user",
            user_id="caller_user",
            bot_id="caller_bot",
            owner_id="caller_owner",
            is_super_admin=False,
            force_upgrade=True,
        )
        logs = caplog.text
        assert "event=expert_chat.caller_connection.request" in logs
        assert "event=expert_chat.caller_connection.success" in logs
        assert "authorized_as=self" in logs
        assert "need_poll=False" in logs
        assert "SENSITIVE_CONNECTION" not in logs
        assert "SENSITIVE_TOKEN" not in logs
        assert "Authorization" not in logs
        assert "Cookie" not in logs

    @pytest.mark.parametrize(
        ("operator_id", "reason", "expected_code"),
        [
            ("anonymous", "missing_operator", 400),
            ("caller_user", "instance_not_found", 403),
        ],
    )
    def test_logs_safe_denial(
        self, caller_connection_client, caplog, operator_id, reason, expected_code
    ):
        client, instance_service, mock_user = caller_connection_client
        mock_user.staffId = operator_id
        error = ChatPermissionError("SENSITIVE_SECRET")
        error.reason = reason
        instance_service.get_authorized_caller_connection.side_effect = error

        with caplog.at_level(logging.INFO):
            response = self._post(client)

        assert response.status_code == 200
        assert response.json()["error_code"] == expected_code
        assert "event=expert_chat.caller_connection.request" in caplog.text
        assert "event=expert_chat.caller_connection.denied" in caplog.text
        assert f"reason={reason}" in caplog.text
        assert "SENSITIVE_SECRET" not in caplog.text
        if expected_code == 400:
            instance_service.get_authorized_caller_connection.assert_not_awaited()
        else:
            instance_service.get_authorized_caller_connection.assert_awaited_once()

    def test_logs_exception_type_without_secret(
        self, caller_connection_client, caplog
    ):
        client, instance_service, _ = caller_connection_client
        instance_service.get_authorized_caller_connection.side_effect = RuntimeError(
            "SENSITIVE_CREDENTIAL"
        )

        with caplog.at_level(logging.INFO):
            response = self._post(client)

        assert response.status_code == 200
        assert response.json()["error_code"] == 5999
        assert "event=expert_chat.caller_connection.failed" in caplog.text
        assert "exception_type=RuntimeError" in caplog.text
        assert "SENSITIVE_CREDENTIAL" not in caplog.text


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

    def test_remove_chat_bot_connection_error_is_retryable(self, client_with_mock):
        """Keep runtime cleanup failures distinguishable from generic errors."""
        client, mock_service = client_with_mock
        mock_service.remove_chat_bot.side_effect = ConnectionError(
            "Bot服务正在启动，请稍后重试",
            error_code="5001",
        )

        response = client.delete("/api/v1/expert-chats/test_bot/test_owner")

        assert response.status_code == 200
        assert response.json() == {
            "success": False,
            "message": "Bot服务正在启动，请稍后重试",
            "error_code": 5001,
            "data": None,
        }


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

    def test_delete_chat_session_maps_unexpected_error(self, client_with_mock):
        client, mock_service = client_with_mock
        mock_service.delete_chat_session.side_effect = RuntimeError("unexpected")

        response = client.delete("/api/v1/expert-chats/test_bot/test_owner/session")

        assert response.status_code == 200
        assert response.json()["error_code"] == 5999


class TestMultiChatSessions:
    """Tests for the multi-session expert-chat endpoints."""

    def test_list_sessions_passes_filters_and_authenticated_user(
        self, client_with_mock
    ):
        client, mock_service = client_with_mock
        client.cookies.set("IAM_TOKEN", "test-iam-token")

        response = client.get(
            "/api/v1/expert-chats/test_bot/test_owner/sessions",
            params={
                "session_key": "session:test",
                "favorite_only": "true",
                "limit": 10,
                "offset": 2,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["items"][0]["last_message"]["content"] == "hello"
        mock_service.list_chat_sessions.assert_awaited_once_with(
            user_id="test_user",
            bot_id="test_bot",
            owner_id="test_owner",
            session_key="session:test",
            favorite_only=True,
            limit=10,
            offset=2,
            iam_token="test-iam-token",
        )

    def test_list_sessions_rejects_invalid_limit(self, client_with_mock):
        client, mock_service = client_with_mock

        response = client.get(
            "/api/v1/expert-chats/test_bot/test_owner/sessions?limit=101"
        )

        assert response.status_code == 422
        mock_service.list_chat_sessions.assert_not_awaited()

    def test_create_session(self, client_with_mock):
        client, mock_service = client_with_mock

        response = client.post("/api/v1/expert-chats/test_bot/test_owner/sessions")

        assert response.status_code == 200
        assert response.json()["data"]["session_key"] == "session:new"
        mock_service.create_chat_session.assert_awaited_once_with(
            user_id="test_user",
            bot_id="test_bot",
            owner_id="test_owner",
            iam_token=None,
        )

    def test_connect_session(self, client_with_mock):
        client, mock_service = client_with_mock

        response = client.post(
            "/api/v1/expert-chats/test_bot/test_owner/sessions/connect",
            json={"session_key": "session:test"},
        )

        assert response.status_code == 200
        assert response.json()["data"]["is_new"] is False
        mock_service.connect_chat_session.assert_awaited_once_with(
            user_id="test_user",
            bot_id="test_bot",
            owner_id="test_owner",
            session_key="session:test",
            iam_token=None,
        )

    def test_delete_session(self, client_with_mock):
        client, mock_service = client_with_mock

        response = client.delete(
            "/api/v1/expert-chats/test_bot/test_owner/sessions",
            params={"session_key": "session:test"},
        )

        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_service.delete_owned_chat_session.assert_awaited_once_with(
            user_id="test_user",
            bot_id="test_bot",
            owner_id="test_owner",
            session_key="session:test",
        )

    def test_delete_unowned_session_returns_not_found(self, client_with_mock):
        client, mock_service = client_with_mock
        mock_service.delete_owned_chat_session.side_effect = BotNotFoundError(
            "Session不存在或不属于当前用户"
        )

        response = client.delete(
            "/api/v1/expert-chats/test_bot/test_owner/sessions",
            params={"session_key": "session:other"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == 404

    @pytest.mark.parametrize(
        ("error", "expected_code"),
        [
            (BotNotFoundError("missing"), 404),
            (BotNotActiveError("inactive"), 400),
            (BotNotPublishedError("unpublished"), 4001),
            (ChatPermissionError("forbidden"), 403),
            (ConnectionError("offline", error_code="5001"), 5001),
            (RuntimeError("unexpected"), 5999),
        ],
    )
    def test_list_sessions_maps_service_errors(
        self, client_with_mock, error, expected_code
    ):
        client, mock_service = client_with_mock
        mock_service.list_chat_sessions.side_effect = error

        response = client.get("/api/v1/expert-chats/test_bot/test_owner/sessions")

        assert response.status_code == 200
        assert response.json()["error_code"] == expected_code

    @pytest.mark.parametrize(
        ("error", "expected_code"),
        [
            (BotNotFoundError("missing"), 404),
            (BotNotActiveError("inactive"), 400),
            (BotNotPublishedError("unpublished"), 4001),
            (ChatPermissionError("forbidden"), 403),
            (SessionCreateError("failed", error_code="5003"), 5003),
            (RuntimeError("unexpected"), 5999),
        ],
    )
    def test_create_session_maps_service_errors(
        self, client_with_mock, error, expected_code
    ):
        client, mock_service = client_with_mock
        mock_service.create_chat_session.side_effect = error

        response = client.post("/api/v1/expert-chats/test_bot/test_owner/sessions")

        assert response.status_code == 200
        assert response.json()["error_code"] == expected_code

    @pytest.mark.parametrize(
        ("error", "expected_code"),
        [
            (BotNotFoundError("missing"), 404),
            (BotNotActiveError("inactive"), 400),
            (BotNotPublishedError("unpublished"), 4001),
            (ChatPermissionError("forbidden"), 403),
            (ConnectionError("offline", error_code="5001"), 5001),
            (RuntimeError("unexpected"), 5999),
        ],
    )
    def test_connect_session_maps_service_errors(
        self, client_with_mock, error, expected_code
    ):
        client, mock_service = client_with_mock
        mock_service.connect_chat_session.side_effect = error

        response = client.post(
            "/api/v1/expert-chats/test_bot/test_owner/sessions/connect",
            json={"session_key": "session:test"},
        )

        assert response.status_code == 200
        assert response.json()["error_code"] == expected_code

    def test_delete_session_maps_unexpected_error(self, client_with_mock):
        client, mock_service = client_with_mock
        mock_service.delete_owned_chat_session.side_effect = RuntimeError("unexpected")

        response = client.delete(
            "/api/v1/expert-chats/test_bot/test_owner/sessions",
            params={"session_key": "session:test"},
        )

        assert response.status_code == 200
        assert response.json()["error_code"] == 5999

    @pytest.mark.parametrize(
        ("error", "expected_code"),
        [
            (BotNotActiveError("inactive"), 400),
            (ChatPermissionError("forbidden"), 403),
            (ConnectionError("offline", error_code="5001"), 5001),
        ],
    )
    def test_delete_session_maps_access_errors(
        self, client_with_mock, error, expected_code
    ):
        client, mock_service = client_with_mock
        mock_service.delete_owned_chat_session.side_effect = error

        response = client.delete(
            "/api/v1/expert-chats/test_bot/test_owner/sessions",
            params={"session_key": "session:test"},
        )

        assert response.status_code == 200
        assert response.json()["error_code"] == expected_code
