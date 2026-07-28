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
from agentclaw.community.core.notify.protocol import NotifyBotLister, NotifyTarget
from agentclaw.community.core.devices.services.device_context import (
    ConnInfoBuildError,
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTimeoutError,
)


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



def _make_ctx(provider="arca", bot_type="personal", conn_info=None):
    """A fake DeviceContext returned by resolver.resolve_for_bot."""
    ctx = MagicMock()
    ctx.provider = provider
    ctx.bot_type = bot_type
    ctx.conn_info = conn_info if conn_info is not None else {
        "url": "http://engine/proxypass/target",
        "headers": {"x-proxypass-token": "t"},
    }
    return ctx


def _make_resolver(ctx=None, exc=None):
    resolver = MagicMock()
    if exc is not None:
        resolver.resolve_for_bot.side_effect = exc
    else:
        resolver.resolve_for_bot.return_value = ctx if ctx is not None else _make_ctx()
    return resolver


def _make_transport(response=None, exc=None):
    transport = MagicMock()
    if exc is not None:
        transport.invoke = AsyncMock(side_effect=exc)
    else:
        transport.invoke = AsyncMock(
            return_value=response if response is not None else {"success": True, "data": []}
        )
    return transport


def _target(bot_id="bot_001", bot_name="Bot One", owner_id="owner1", sandbox_id="sb1"):
    return NotifyTarget(
        bot_id=bot_id, bot_name=bot_name, owner_id=owner_id, sandbox_id=sandbox_id,
    )


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
    """Test _probe_bot_notify — provider-routed via resolver + transport."""

    @pytest.mark.asyncio
    async def test_probe_success_with_notifications(self):
        data = [
            {
                "interactionId": "123",
                "sessionKey": "session_001",
                "runId": "run_001",
                "kind": "confirm",
                "prompt": "Confirm action?",
                "status": "pending",
                "createdAtMs": 1234567890,
            }
        ]
        result = await _probe_bot_notify(
            _target("bot_001", "Bot One"),
            _make_resolver(),
            _make_transport({"success": True, "data": data}),
        )

        assert result is not None
        assert result.bot_id == "bot_001"
        assert result.sandbox_id == "sb1"
        assert len(result.notifications) == 1
        assert result.notifications[0].interactionId == "123"
        assert result.notifications[0].kind == "confirm"

    @pytest.mark.asyncio
    async def test_probe_success_empty_notifications(self):
        result = await _probe_bot_notify(
            _target("bot_002", "Bot Two", sandbox_id="sandbox_002"),
            _make_resolver(),
            _make_transport({"success": True, "data": []}),
        )

        assert result is not None
        assert result.bot_id == "bot_002"
        assert result.notifications == []

    @pytest.mark.asyncio
    async def test_probe_desktop_routes_through_transport(self):
        """A desktop (BaaS) bot must probe via the transport's invoke-http path,
        not the legacy Arca proxypass — this is the regression the fix targets.
        Before the fix every BaaS bot produced ENGINE_HTTP_500."""
        resolver = _make_resolver(_make_ctx(
            provider="baas",
            bot_type="desktop",
            conn_info={
                "url": "ignored-by-desktop-branch",
                "type": "desktop",
                "binding_id": 7,
                "bot_uuid": "BOT-xyz",
                "tenant": "t",
                "baas_base_url": "http://baas",
                "engine_port": 20003,
                "headers": {},
            },
        ))
        transport = _make_transport({"success": True, "data": []})

        result = await _probe_bot_notify(
            _target("bot_desk", "Desktop Bot"), resolver, transport
        )

        assert result is not None
        assert result.bot_id == "bot_desk"
        assert result.notifications == []
        transport.invoke.assert_awaited_once()
        args, _kwargs = transport.invoke.call_args
        # (conn_info, method, path, ...) — method/path must target /api/notify
        assert args[1] == "GET"
        assert args[2] == "/api/notify"
        # conn_info flows through so the transport can pick the desktop branch
        assert args[0]["type"] == "desktop"

    @pytest.mark.asyncio
    async def test_probe_not_ok(self):
        result = await _probe_bot_notify(
            _target("bot_004", "Bot Four"),
            _make_resolver(),
            _make_transport({"success": False, "error": "Connection failed"}),
        )

        assert result is not None
        assert result.notifications == []
        assert result.status == "error"
        assert result.error_code == "ENGINE_NOTIFY_ERROR"
        assert result.error_message == "Connection failed"

    @pytest.mark.asyncio
    async def test_probe_preserves_engine_error_code(self):
        result = await _probe_bot_notify(
            _target("bot_relay", "Relay Bot", sandbox_id="sb_relay"),
            _make_resolver(),
            _make_transport({"success": False, "data": [], "message": "RELAY_UNAVAILABLE: relay down"}),
        )

        assert result is not None
        assert result.status == "error"
        assert result.error_code == "RELAY_UNAVAILABLE"
        assert result.error_message == "relay down"
        assert result.notifications == []

    @pytest.mark.asyncio
    async def test_probe_transport_exception(self):
        result = await _probe_bot_notify(
            _target("bot_005", "Bot Five"),
            _make_resolver(),
            _make_transport(exc=Exception("Network error")),
        )

        assert result is not None
        assert result.bot_id == "bot_005"
        assert result.notifications == []
        assert result.status == "error"
        assert result.error_code == "NOTIFY_PROBE_ERROR"
        assert result.error_message == "Network error"

    @pytest.mark.asyncio
    async def test_probe_http_500(self):
        result = await _probe_bot_notify(
            _target("bot_007", "Bot Seven"),
            _make_resolver(),
            _make_transport(exc=DeviceAdapterHTTPStatusError(500, "boom")),
        )

        assert result is not None
        assert result.bot_id == "bot_007"
        assert result.notifications == []
        assert result.status == "error"
        assert result.error_code == "ENGINE_HTTP_500"

    @pytest.mark.asyncio
    async def test_probe_http_404(self):
        result = await _probe_bot_notify(
            _target("bot_404", "Bot 404"),
            _make_resolver(),
            _make_transport(exc=DeviceAdapterEndpointNotFoundError("no endpoint")),
        )

        assert result is not None
        assert result.error_code == "ENGINE_HTTP_404"

    @pytest.mark.asyncio
    async def test_probe_timeout(self):
        result = await _probe_bot_notify(
            _target("bot_to", "Bot TO"),
            _make_resolver(),
            _make_transport(exc=DeviceAdapterTimeoutError("timed out")),
        )

        assert result is not None
        assert result.error_code == "ENGINE_TIMEOUT"

    @pytest.mark.asyncio
    async def test_probe_device_not_bound_returns_empty(self):
        result = await _probe_bot_notify(
            _target("bot_nb", "Bot NB"),
            _make_resolver(exc=DeviceNotBoundError("no binding")),
            _make_transport(),
        )

        assert result is not None
        assert result.bot_id == "bot_nb"
        assert result.notifications == []
        # bot exists but (transiently) has no active binding → benign empty
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_probe_resolve_error(self):
        result = await _probe_bot_notify(
            _target("bot_re", "Bot RE"),
            _make_resolver(exc=ConnInfoBuildError("build failed")),
            _make_transport(),
        )

        assert result is not None
        assert result.status == "error"
        assert result.error_code == "NOTIFY_RESOLVE_ERROR"

    @pytest.mark.asyncio
    async def test_probe_unknown_provider(self):
        result = await _probe_bot_notify(
            _target("bot_up", "Bot UP"),
            _make_resolver(exc=UnknownProviderError("weird provider")),
            _make_transport(),
        )

        assert result is not None
        assert result.error_code == "NOTIFY_RESOLVE_ERROR"

    @pytest.mark.asyncio
    async def test_probe_local_mode(self):
        """provider=local → direct httpx to 127.0.0.1:20003 (legacy local dev)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "data": []}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _probe_bot_notify(
                _target("bot_006", "Bot Six", sandbox_id="local"),
                _make_resolver(_make_ctx(provider="local")),
                _make_transport(),
            )

        assert result is not None
        assert result.bot_id == "bot_006"
        assert result.sandbox_id == "local"
        mock_client.get.assert_called_once()
        assert "127.0.0.1:20003" in str(mock_client.get.call_args)

    @pytest.mark.asyncio
    async def test_probe_local_non_200(self):
        mock_response = MagicMock()
        mock_response.status_code = 503

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _probe_bot_notify(
                _target("bot_l", "Bot L", sandbox_id="local"),
                _make_resolver(_make_ctx(provider="local")),
                _make_transport(),
            )

        assert result is not None
        assert result.error_code == "ENGINE_HTTP_503"


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
            NotifyTarget("bot_001", "Test Bot", "testuser", "sandbox_001"),
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
            NotifyTarget("bot_001", "Bot One", "testuser", "sandbox_001"),
            NotifyTarget("bot_002", "Bot Two", "testuser", "sandbox_002"),
        ]
        call_count = 0

        async def mock_probe(target, resolver, transport):
            nonlocal call_count
            call_count += 1
            return BotNotifySummary(
                bot_id=target.bot_id,
                bot_name=target.bot_name,
                sandbox_id=target.sandbox_id,
                notifications=[
                    NotifyEntry(
                        interactionId=f"int_{target.bot_id}",
                        sessionKey=f"session_{target.bot_id}",
                        runId=f"run_{target.bot_id}",
                        kind="confirm",
                        prompt=f"Prompt for {target.bot_id}",
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
            NotifyTarget("bot_full", "Full Bot", "testuser", "sb_full"),
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

        async def mock_probe(target, resolver, transport):
            return BotNotifySummary(
                bot_id=target.bot_id, bot_name=target.bot_name,
                sandbox_id=target.sandbox_id, notifications=[notification],
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
            NotifyTarget("bot_ok", "OK Bot", "testuser", "sb_ok"),
            NotifyTarget("bot_fail", "Fail Bot", "testuser", "sb_fail"),
        ]

        async def mock_probe(target, resolver, transport):
            if target.bot_id == "bot_fail":
                return None
            return BotNotifySummary(
                bot_id=target.bot_id,
                bot_name=target.bot_name,
                sandbox_id=target.sandbox_id,
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
        result = await _probe_bot_notify(
            _target("bot_e1", "Bot E1"),
            _make_resolver(),
            _make_transport({"success": True, "data": {"status": "ok", "count": 0}}),
        )

        assert result is not None
        assert result.notifications == []

    @pytest.mark.asyncio
    async def test_probe_data_is_none(self):
        result = await _probe_bot_notify(
            _target("bot_none", "Bot None"),
            _make_resolver(),
            _make_transport({"success": True, "data": None}),
        )

        assert result is not None
        assert result.notifications == []

    @pytest.mark.asyncio
    async def test_probe_multiple_notifications(self):
        data = [
            {"interactionId": "n1", "sessionKey": "s1", "runId": "r1", "kind": "confirm", "prompt": "Confirm?", "status": "pending", "createdAtMs": 1000},
            {"interactionId": "n2", "sessionKey": "s2", "runId": "r2", "kind": "input", "prompt": "Enter value:", "status": "active", "createdAtMs": 2000, "expiresAtMs": 9000},
            {"interactionId": "n3", "sessionKey": "s3", "runId": "r3", "kind": "select", "options": [{"value": "a"}, {"value": "b"}], "status": "pending", "createdAtMs": 3000},
        ]
        result = await _probe_bot_notify(
            _target("bot_m", "Multi Bot"),
            _make_resolver(),
            _make_transport({"success": True, "data": data}),
        )

        assert result is not None
        assert len(result.notifications) == 3
        assert result.notifications[0].kind == "confirm"
        assert result.notifications[1].kind == "input"
        assert result.notifications[1].expiresAtMs == 9000
        assert result.notifications[2].kind == "select"
        assert len(result.notifications[2].options) == 2

    @pytest.mark.asyncio
    async def test_probe_response_missing_success_key(self):
        result = await _probe_bot_notify(
            _target("bot_no_ok", "Bot NoOK"),
            _make_resolver(),
            _make_transport({"data": []}),
        )

        assert result is not None
        assert result.notifications == []
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_probe_invalid_notification_data_returns_probe_error(self):
        """Malformed notification payload → caught and surfaced as NOTIFY_PROBE_ERROR."""
        result = await _probe_bot_notify(
            _target("bot_bad", "Bot Bad"),
            _make_resolver(),
            _make_transport({"success": True, "data": [{"invalid_key": "no required fields"}]}),
        )

        assert result is not None
        assert result.notifications == []
        assert result.error_code == "NOTIFY_PROBE_ERROR"

