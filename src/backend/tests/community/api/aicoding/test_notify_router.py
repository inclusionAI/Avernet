"""Tests for Notify API router."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.aicoding.notify_router import (
    router,
    _probe_bot_notify,
    NotifyEntry,
    BotNotifySummary,
    NotifySummaryResponse,
)
from agentclaw.community.core.auth.models import AuthenticatedIdentity
from agentclaw.community.core.notify.protocol import NotifyBotLister


def _create_notify_client(test_injector, mock_user, mock_bot_lister):
    """Create a test client with a mock NotifyBotLister in the injector."""
    from agentclaw.community.adapters.http.auth.dependencies import get_current_user

    app = FastAPI()
    app.include_router(router)

    class _TestModule(Module):
        def configure(self, binder):
            binder.bind(NotifyBotLister, to=mock_bot_lister)

    injector = Injector([_TestModule()], parent=test_injector)
    attach_injector(app, injector)
    app.dependency_overrides[get_current_user] = lambda: mock_user

    return TestClient(app)


@pytest.fixture
def mock_bot_lister():
    """Create a mock NotifyBotLister."""
    return MagicMock(spec=NotifyBotLister)


@pytest.fixture
def mock_user():
    user = MagicMock(spec=AuthenticatedIdentity)
    user.staffId = "testuser"
    user.operatorName = "Test User"
    user.nickName = "Test User"
    return user


class TestNotifyModels:
    """Test model validation."""

    def test_notify_entry_creation(self):
        entry = NotifyEntry(
            interactionId="123",
            sessionKey="session_001",
            runId="run_001",
            kind="confirm",
            prompt="Are you sure?",
            status="pending",
            createdAtMs=1234567890,
        )
        assert entry.interactionId == "123"
        assert entry.kind == "confirm"
        assert entry.prompt == "Are you sure?"

    def test_notify_entry_with_optional_fields(self):
        entry = NotifyEntry(
            interactionId="456",
            sessionKey="session_002",
            runId="run_002",
            kind="select",
            prompt="Choose an option",
            options=[{"value": "a", "label": "Option A"}],
            questions=[{"text": "Question 1"}],
            subject={"type": "test"},
            command="ls",
            cwd="/home",
            status="active",
            createdAtMs=1234567890,
            expiresAtMs=1234569900,
        )
        assert entry.options is not None
        assert len(entry.options) == 1
        assert entry.expiresAtMs == 1234569900

    def test_bot_notify_summary(self):
        summary = BotNotifySummary(
            bot_id="bot_001",
            bot_name="Test Bot",
            sandbox_id="sandbox_001",
            notifications=[],
        )
        assert summary.bot_id == "bot_001"
        assert summary.notifications == []

    def test_notify_summary_response(self):
        response = NotifySummaryResponse(success=True, data=[])
        assert response.success is True
        assert response.data == []


class TestProbeBotNotify:
    """Test the _probe_bot_notify function."""

    @pytest.mark.asyncio
    async def test_probe_bot_notify_success_with_notifications(self):
        """Test successful probe with notifications."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": [
                {
                    "interactionId": "123",
                    "sessionKey": "session_001",
                    "runId": "run_001",
                    "kind": "confirm",
                    "prompt": "Confirm action?",
                    "status": "pending",
                    "createdAtMs": 1234567890,
                }
            ],
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await _probe_bot_notify("bot_001", "Bot One", "sandbox_001", MagicMock())

        assert result is not None
        assert result.bot_id == "bot_001"
        assert result.bot_name == "Bot One"
        assert result.sandbox_id == "sandbox_001"
        assert len(result.notifications) == 1
        assert result.notifications[0].interactionId == "123"
        assert result.notifications[0].kind == "confirm"

    @pytest.mark.asyncio
    async def test_probe_bot_notify_success_empty_notifications(self):
        """Test successful probe with empty notifications."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": [],
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await _probe_bot_notify("bot_002", "Bot Two", "sandbox_002", MagicMock())

        assert result is not None
        assert result.bot_id == "bot_002"
        assert result.notifications == []

    @pytest.mark.asyncio
    async def test_probe_bot_notify_data_is_list(self):
        """Test probe with data as list."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": [],
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await _probe_bot_notify("bot_003", "Bot Three", "sandbox_003", MagicMock())

        assert result is not None
        assert result.notifications == []

    @pytest.mark.asyncio
    async def test_probe_bot_notify_not_ok(self):
        """Test probe returns not success."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": False,
            "error": "Connection failed",
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await _probe_bot_notify("bot_004", "Bot Four", "sandbox_004", MagicMock())

        assert result is not None
        assert result.notifications == []

    @pytest.mark.asyncio
    async def test_probe_bot_notify_exception(self):
        """Test probe raises exception."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=Exception("Network error"))

        with patch(
            "httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await _probe_bot_notify("bot_005", "Bot Five", "sandbox_005", MagicMock())

        assert result is not None
        assert result.bot_id == "bot_005"
        assert result.notifications == []

    @pytest.mark.asyncio
    async def test_probe_bot_notify_local_mode(self):
        """Test probe in local mode (sandbox_id is 'local')."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": [],
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await _probe_bot_notify("bot_006", "Bot Six", "local", MagicMock())

        assert result is not None
        assert result.bot_id == "bot_006"
        assert result.sandbox_id == "local"
        # Verify it called the local URL
        mock_client.get.assert_called_once()
        call_args = mock_client.get.call_args
        assert "127.0.0.1:20003" in str(call_args)

    @pytest.mark.asyncio
    async def test_probe_bot_notify_non_200_status(self):
        """Test probe returns non-200 status."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await _probe_bot_notify("bot_007", "Bot Seven", "local", MagicMock())

        assert result is not None
        assert result.bot_id == "bot_007"
        assert result.notifications == []


class TestGetNotifySummaryNoBots:
    """Test the GET /api/v1/notify endpoint when no bots found."""

    def test_no_bots_returns_empty(self, test_injector, mock_user, mock_bot_lister):
        mock_bot_lister.list_bot_mappings.return_value = []
        client = _create_notify_client(test_injector, mock_user, mock_bot_lister)

        resp = client.get("/api/v1/notify")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"] == []
        mock_bot_lister.list_bot_mappings.assert_called_once_with("testuser")


class TestGetNotifySummaryWithBots:
    """Test the GET /api/v1/notify endpoint with bots."""

    def test_single_bot(self, test_injector, mock_user, mock_bot_lister):
        mock_bot_lister.list_bot_mappings.return_value = [
            ("bot_001", "Test Bot", "sandbox_001"),
        ]
        mock_summary = BotNotifySummary(
            bot_id="bot_001",
            bot_name="Test Bot",
            sandbox_id="sandbox_001",
            notifications=[],
        )
        client = _create_notify_client(test_injector, mock_user, mock_bot_lister)

        with patch("agentclaw.community.adapters.http.aicoding.notify_router._probe_bot_notify",
                   new_callable=AsyncMock, return_value=mock_summary):
            resp = client.get("/api/v1/notify")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["bot_name"] == "Test Bot"

    def test_multiple_bots(self, test_injector, mock_user, mock_bot_lister):
        mock_bot_lister.list_bot_mappings.return_value = [
            ("bot_001", "Bot One", "sandbox_001"),
            ("bot_002", "Bot Two", "sandbox_002"),
        ]
        call_count = 0

        async def mock_probe(bot_id, bot_name, sandbox_id, sandbox_client=None):
            nonlocal call_count
            call_count += 1
            return BotNotifySummary(
                bot_id=bot_id,
                bot_name=bot_name,
                sandbox_id=sandbox_id,
                notifications=[
                    NotifyEntry(
                        interactionId=f"int_{bot_id}",
                        sessionKey=f"session_{bot_id}",
                        runId=f"run_{bot_id}",
                        kind="confirm",
                        prompt=f"Prompt for {bot_id}",
                        status="pending",
                        createdAtMs=1234567890,
                    )
                ],
            )

        client = _create_notify_client(test_injector, mock_user, mock_bot_lister)

        with patch("agentclaw.community.adapters.http.aicoding.notify_router._probe_bot_notify",
                   side_effect=mock_probe):
            resp = client.get("/api/v1/notify")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]) == 2
        assert call_count == 2
        bot_ids = {d["bot_id"] for d in data["data"]}
        assert bot_ids == {"bot_001", "bot_002"}

    def test_probe_returns_none_filtered(self, test_injector, mock_user, mock_bot_lister):
        mock_bot_lister.list_bot_mappings.return_value = [
            ("bot_001", "Bot One", "sandbox_001"),
        ]
        client = _create_notify_client(test_injector, mock_user, mock_bot_lister)

        with patch("agentclaw.community.adapters.http.aicoding.notify_router._probe_bot_notify",
                   new_callable=AsyncMock, return_value=None):
            resp = client.get("/api/v1/notify")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"] == []

    def test_with_notifications_in_response(self, test_injector, mock_user, mock_bot_lister):
        mock_bot_lister.list_bot_mappings.return_value = [
            ("bot_full", "Full Bot", "sb_full"),
        ]
        notification = NotifyEntry(
            interactionId="int_full",
            sessionKey="sk_full",
            runId="rn_full",
            kind="confirm",
            prompt="Do you approve?",
            status="pending",
            createdAtMs=9999999,
            expiresAtMs=19999999,
        )

        async def mock_probe(bot_id, bot_name, sandbox_id, sandbox_client=None):
            return BotNotifySummary(
                bot_id=bot_id, bot_name=bot_name,
                sandbox_id=sandbox_id, notifications=[notification],
            )

        client = _create_notify_client(test_injector, mock_user, mock_bot_lister)

        with patch("agentclaw.community.adapters.http.aicoding.notify_router._probe_bot_notify",
                   side_effect=mock_probe):
            resp = client.get("/api/v1/notify")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["bot_name"] == "Full Bot"
        notifications = data["data"][0]["notifications"]
        assert len(notifications) == 1
        assert notifications[0]["interactionId"] == "int_full"
        assert notifications[0]["prompt"] == "Do you approve?"
        assert notifications[0]["expiresAtMs"] == 19999999

    def test_mixed_probe_results(self, test_injector, mock_user, mock_bot_lister):
        """One probe succeeds, one returns None — only successful one in output."""
        mock_bot_lister.list_bot_mappings.return_value = [
            ("bot_ok", "OK Bot", "sb_ok"),
            ("bot_fail", "Fail Bot", "sb_fail"),
        ]

        async def mock_probe(bot_id, bot_name, sandbox_id, sandbox_client=None):
            if bot_id == "bot_fail":
                return None
            return BotNotifySummary(
                bot_id=bot_id,
                bot_name=bot_name,
                sandbox_id=sandbox_id,
                notifications=[],
            )

        client = _create_notify_client(test_injector, mock_user, mock_bot_lister)

        with patch("agentclaw.community.adapters.http.aicoding.notify_router._probe_bot_notify",
                   side_effect=mock_probe):
            resp = client.get("/api/v1/notify")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["bot_id"] == "bot_ok"


class TestIntegrationWithDI:
    """Test integration with dependency injection."""

    @pytest.mark.asyncio
    async def test_notify_entry_accepts_various_types(self):
        entry1 = NotifyEntry(
            interactionId="1",
            sessionKey="s1",
            runId="r1",
            kind="confirm",
            status="pending",
            createdAtMs=1234567890,
        )
        assert entry1.createdAtMs == 1234567890

        entry2 = NotifyEntry(
            interactionId="2",
            sessionKey="s2",
            runId="r2",
            kind="input",
            prompt="Enter value:",
            questions=[{"id": 1, "text": "Value?"}],
            options=[{"value": "x", "label": "X"}],
            subject={"foo": "bar"},
            command="run",
            cwd="/tmp",
            status="active",
            createdAtMs=1234567890,
            expiresAtMs=1234999999,
        )
        assert entry2.questions is not None
        assert entry2.expiresAtMs is not None


class TestSerializeDeserialize:
    """Test serialization and deserialization of models."""

    def test_notify_entry_dict_roundtrip(self):
        data = {
            "interactionId": "123",
            "sessionKey": "session_001",
            "runId": "run_001",
            "kind": "confirm",
            "prompt": "Are you sure?",
            "status": "pending",
            "createdAtMs": 1234567890,
        }
        entry = NotifyEntry(**data)
        assert entry.interactionId == "123"

    def test_bot_notify_summary_serialization(self):
        summary = BotNotifySummary(
            bot_id="bot_001",
            bot_name="Test Bot",
            sandbox_id="sandbox_001",
            notifications=[],
        )
        result = summary.model_dump()
        assert result["bot_id"] == "bot_001"
        assert result["notifications"] == []

    def test_bot_notify_summary_with_nested_notifications(self):
        entry = NotifyEntry(
            interactionId="123",
            sessionKey="session_001",
            runId="run_001",
            kind="confirm",
            prompt="Are you sure?",
            status="pending",
            createdAtMs=1234567890,
        )
        summary = BotNotifySummary(
            bot_id="bot_001",
            bot_name="Test Bot",
            sandbox_id="sandbox_001",
            notifications=[entry],
        )
        result = summary.model_dump()
        assert len(result["notifications"]) == 1
        assert result["notifications"][0]["interactionId"] == "123"

    def test_notify_summary_response_with_data(self):
        entry = NotifyEntry(
            interactionId="123",
            sessionKey="s1",
            runId="r1",
            kind="confirm",
            status="pending",
            createdAtMs=1234567890,
        )
        summary = BotNotifySummary(
            bot_id="bot_001",
            bot_name="Bot",
            sandbox_id="sb_001",
            notifications=[entry],
        )
        response = NotifySummaryResponse(success=True, data=[summary])
        result = response.model_dump()
        assert result["success"] is True
        assert len(result["data"]) == 1
        assert result["data"][0]["bot_id"] == "bot_001"
        assert len(result["data"][0]["notifications"]) == 1


class TestProbeBotNotifyEdgeCases:
    """Additional edge cases for _probe_bot_notify."""

    @pytest.mark.asyncio
    async def test_probe_data_not_list_returns_empty(self):
        """Test when data is a dict instead of list - should return empty notifications."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {"status": "ok", "count": 0},
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await _probe_bot_notify("bot_e1", "Bot E1", "sandbox_e1", MagicMock())

        assert result is not None
        assert result.notifications == []

    @pytest.mark.asyncio
    async def test_probe_data_is_none(self):
        """Test when data field is None - should return empty notifications."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "data": None}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await _probe_bot_notify("bot_none", "Bot None", "sb_none", MagicMock())

        assert result is not None
        assert result.notifications == []

    @pytest.mark.asyncio
    async def test_probe_multiple_notifications(self):
        """Test with multiple notifications in response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": [
                {
                    "interactionId": "n1",
                    "sessionKey": "s1",
                    "runId": "r1",
                    "kind": "confirm",
                    "prompt": "Confirm?",
                    "status": "pending",
                    "createdAtMs": 1000,
                },
                {
                    "interactionId": "n2",
                    "sessionKey": "s2",
                    "runId": "r2",
                    "kind": "input",
                    "prompt": "Enter value:",
                    "status": "active",
                    "createdAtMs": 2000,
                    "expiresAtMs": 9000,
                },
                {
                    "interactionId": "n3",
                    "sessionKey": "s3",
                    "runId": "r3",
                    "kind": "select",
                    "options": [{"value": "a"}, {"value": "b"}],
                    "status": "pending",
                    "createdAtMs": 3000,
                },
            ],
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await _probe_bot_notify("bot_m", "Multi Bot", "sandbox_m", MagicMock())

        assert result is not None
        assert len(result.notifications) == 3
        assert result.notifications[0].kind == "confirm"
        assert result.notifications[1].kind == "input"
        assert result.notifications[1].expiresAtMs == 9000
        assert result.notifications[2].kind == "select"
        assert len(result.notifications[2].options) == 2

    @pytest.mark.asyncio
    async def test_probe_exception_building_proxy_request(self):
        """Exception building the proxy request → empty summary (swallowed)."""
        client = MagicMock()
        client.build_proxy_request.side_effect = Exception("proxy build failed")
        result = await _probe_bot_notify("bot_ex1", "Bot EX1", "sandbox_ex1", client)

        assert result is not None
        assert result.bot_id == "bot_ex1"
        assert result.notifications == []

    @pytest.mark.asyncio
    async def test_probe_exception_building_proxy_request_headers(self):
        """A second proxy-build failure path → empty summary (swallowed)."""
        client = MagicMock()
        client.build_proxy_request.side_effect = RuntimeError("no token")
        result = await _probe_bot_notify("bot_ex2", "Bot EX2", "sandbox_ex2", client)

        assert result is not None
        assert result.bot_id == "bot_ex2"
        assert result.notifications == []

    @pytest.mark.asyncio
    async def test_probe_response_missing_success_key(self):
        """Test response missing success key - should return empty notifications."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await _probe_bot_notify("bot_no_ok", "Bot NoOK", "sb_no_ok", MagicMock())

        assert result is not None
        assert result.notifications == []

    @pytest.mark.asyncio
    async def test_probe_invalid_notification_data_raises_exception(self):
        """Test invalid notification data - exception should be caught and return empty."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": [
                {"invalid_key": "no required fields"},
            ]
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await _probe_bot_notify("bot_bad", "Bot Bad", "sb_bad", MagicMock())

        assert result is not None
        # Invalid notification data causes exception which returns empty list
        assert result.notifications == []

    @pytest.mark.asyncio
    async def test_probe_uses_client_proxy_request_url(self):
        """The probe GETs the URL the SandboxRuntimeClient builds for /api/notify."""
        from agentclaw.community.kernel.device_dto import ProxyRequest

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "data": []}
        captured_url = None

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        async def capture_get(url, headers=None):
            nonlocal captured_url
            captured_url = url
            return mock_response

        mock_client.get = AsyncMock(side_effect=capture_get)

        sandbox_client = MagicMock()
        sandbox_client.build_proxy_request.return_value = ProxyRequest(
            url="http://base.local:8080/proxypass/my-target/api/notify",
            headers={"x-proxypass-token": "t"},
        )

        with patch(
            "httpx.AsyncClient",
            return_value=mock_client,
        ):
            await _probe_bot_notify("bot_url", "Bot URL", "sb_url", sandbox_client)

        sandbox_client.build_proxy_request.assert_called_once_with(
            sandbox_id="sb_url", api_path="/api/notify"
        )
        assert captured_url == "http://base.local:8080/proxypass/my-target/api/notify"
