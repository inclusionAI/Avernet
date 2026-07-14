"""Tests for TeClawPaasService — delegation verification.

Verifies that TeClawPaasService correctly delegates all operations to its
plugin and performs domain<->primitive type conversion at the boundary.
HTTP mocking belongs to the plugin layer (test_real.py, test_stub.py).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.community.api.device_manage import (
    ErrorCode,
    PaasError,
    TeClawCreateConfig,
    TeClawCreationResult,
    TeClawCredentials,
    TeClawDeviceInfo,
)
from secbaas.community.api.tenant_manage import TenantType
from secbaas.community.core.service.paas._teclaw_paas_service import TeClawPaasService
from secbaas.community.spi.bot.teclaw._protocols import TeClawBotPlugin
from secbaas.community.spi.bot.teclaw._types import (
    _BotCreateResult,
    _BotDestroyResult,
    _BotInfo,
    _BotRestartResult,
    _BotUpdateResult,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def teclaw_credentials():
    return TeClawCredentials(
        teclaw_endpoint="http://teclaw.test:8080",
        template_id=1,
        template_uuid="tpl-test-001",
    )


@pytest.fixture
def mock_plugin():
    """Return a mock TeclawBotPlugin with all async methods."""
    plugin = MagicMock(spec=TeClawBotPlugin)
    plugin.create_bot = AsyncMock()
    plugin.destroy_bot = AsyncMock()
    plugin.update_bot = AsyncMock()
    plugin.restart_bot = AsyncMock()
    plugin.get_bot = AsyncMock()
    plugin.resolve_http_conn_info = AsyncMock()
    plugin.resolve_ws_conn_info = AsyncMock()
    plugin.close = AsyncMock()
    plugin.update_outbound_rule = AsyncMock()
    return plugin


@pytest.fixture
def service(mock_plugin, teclaw_credentials):
    """Return a TeClawPaasService backed by a mock plugin."""
    return TeClawPaasService(plugin=mock_plugin, credentials=teclaw_credentials)


# ---------------------------------------------------------------------------
# Test metadata methods
# ---------------------------------------------------------------------------


class TestGetCredentialsAndPlatform:
    @pytest.mark.asyncio
    async def test_get_credentials_returns_creds(self, service, teclaw_credentials):
        result = await service.get_credentials()
        assert result is teclaw_credentials

    @pytest.mark.asyncio
    async def test_get_platform_type_returns_teclaw(self, service):
        result = await service.get_platform_type()
        assert result == TenantType.TECLAW


# ---------------------------------------------------------------------------
# Test create_device (delegation + conversion)
# ---------------------------------------------------------------------------


class TestCreateDevice:
    @pytest.mark.asyncio
    async def test_delegates_to_plugin(self, service, mock_plugin):
        """Verify plugin.create_bot called with config.teclaw_bot_config."""
        mock_plugin.create_bot.return_value = _BotCreateResult(
            teclaw_bot_id="bot-abc123",
            status="ONLINE",
            teclaw_bot_config={"key": "value"},
        )
        config = TeClawCreateConfig(teclaw_bot_config={"key": "value"})
        result = await service.create_device(config)
        mock_plugin.create_bot.assert_awaited_once_with(bot_config={"key": "value"})

    @pytest.mark.asyncio
    async def test_converts_result_to_creation_result(self, service, mock_plugin):
        """Mock _BotCreateResult, verify TeClawCreationResult field mapping."""
        mock_plugin.create_bot.return_value = _BotCreateResult(
            teclaw_bot_id="bot-abc123",
            status="ONLINE",
            teclaw_bot_config={"model": "gpt-4"},
        )
        config = TeClawCreateConfig(teclaw_bot_config={"model": "gpt-4"})
        result = await service.create_device(config)
        assert isinstance(result, TeClawCreationResult)
        assert result.teclaw_bot_id == "bot-abc123"
        assert result.platform == "teclaw"
        assert result.status == "ONLINE"
        assert result.teclaw_bot_config == {"model": "gpt-4"}

    @pytest.mark.asyncio
    async def test_handles_empty_config(self, service, mock_plugin):
        """config.teclaw_bot_config is None -> bot_config={}."""
        mock_plugin.create_bot.return_value = _BotCreateResult(
            teclaw_bot_id="bot-xyz",
            status="ONLINE",
        )
        config = TeClawCreateConfig(teclaw_bot_config=None)
        result = await service.create_device(config)
        mock_plugin.create_bot.assert_awaited_once_with(bot_config={})
        assert result.teclaw_bot_id == "bot-xyz"


# ---------------------------------------------------------------------------
# Test destroy_device (delegation)
# ---------------------------------------------------------------------------


class TestDestroyDevice:
    @pytest.mark.asyncio
    async def test_delegates_to_plugin(self, service, mock_plugin):
        """Verify plugin.destroy_bot(bot_id=paas_device_id)."""
        mock_plugin.destroy_bot.return_value = _BotDestroyResult(
            teclaw_bot_id="bot-abc123",
            status="DELETED",
        )
        result = await service.destroy_device("bot-abc123")
        mock_plugin.destroy_bot.assert_awaited_once_with(bot_id="bot-abc123")

    @pytest.mark.asyncio
    async def test_returns_true_when_deleted(self, service, mock_plugin):
        """Mock status='DELETED', verify returns True."""
        mock_plugin.destroy_bot.return_value = _BotDestroyResult(
            teclaw_bot_id="bot-abc123",
            status="DELETED",
        )
        result = await service.destroy_device("bot-abc123")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_not_deleted(self, service, mock_plugin):
        """Mock status='ONLINE', verify returns False."""
        mock_plugin.destroy_bot.return_value = _BotDestroyResult(
            teclaw_bot_id="bot-abc123",
            status="ONLINE",
        )
        result = await service.destroy_device("bot-abc123")
        assert result is False


# ---------------------------------------------------------------------------
# Test update_device (delegation + validation)
# ---------------------------------------------------------------------------


class TestUpdateDevice:
    @pytest.mark.asyncio
    async def test_delegates_to_plugin(self, service, mock_plugin):
        """Verify plugin.update_bot called with correct args."""
        mock_plugin.update_bot.return_value = _BotUpdateResult(
            teclaw_bot_id="bot-abc123",
            status="ONLINE",
            teclaw_bot_config={"updated": True},
        )
        config = TeClawCreateConfig(teclaw_bot_config={"updated": True})
        result = await service.update_device("bot-abc123", config)
        mock_plugin.update_bot.assert_awaited_once_with(
            bot_id="bot-abc123",
            bot_config={"updated": True},
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_raises_when_config_none(self, service):
        """config=None -> PaasError CONFIG_INVALID."""
        with pytest.raises(PaasError) as exc_info:
            await service.update_device("bot-abc123", None)
        assert exc_info.value.code == ErrorCode.CONFIG_INVALID
        assert "TeClawCreateConfig" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_raises_when_config_wrong_type(self, service):
        """Non-TeClawCreateConfig -> PaasError CONFIG_INVALID."""
        with pytest.raises(PaasError) as exc_info:
            await service.update_device("bot-abc123", "not-a-config")
        assert exc_info.value.code == ErrorCode.CONFIG_INVALID


# ---------------------------------------------------------------------------
# Test restart_device (delegation)
# ---------------------------------------------------------------------------


class TestRestartDevice:
    @pytest.mark.asyncio
    async def test_delegates_to_plugin(self, service, mock_plugin):
        """Verify plugin.restart_bot called."""
        mock_plugin.restart_bot.return_value = _BotRestartResult(
            teclaw_bot_id="bot-abc123",
            status="ONLINE",
        )
        result = await service.restart_device("bot-abc123")
        mock_plugin.restart_bot.assert_awaited_once_with(bot_id="bot-abc123")

    @pytest.mark.asyncio
    async def test_returns_true_when_online(self, service, mock_plugin):
        """Mock status='ONLINE', verify True."""
        mock_plugin.restart_bot.return_value = _BotRestartResult(
            teclaw_bot_id="bot-abc123",
            status="ONLINE",
        )
        result = await service.restart_device("bot-abc123")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_not_online(self, service, mock_plugin):
        """Mock status='OFFLINE', verify False."""
        mock_plugin.restart_bot.return_value = _BotRestartResult(
            teclaw_bot_id="bot-abc123",
            status="OFFLINE",
        )
        result = await service.restart_device("bot-abc123")
        assert result is False


# ---------------------------------------------------------------------------
# Test get_device_info (delegation + conversion)
# ---------------------------------------------------------------------------


class TestGetDeviceInfo:
    @pytest.mark.asyncio
    async def test_delegates_to_plugin(self, service, mock_plugin):
        """Verify plugin.get_bot called."""
        mock_plugin.get_bot.return_value = _BotInfo(
            teclaw_bot_id="bot-abc123",
            status="ONLINE",
            teclaw_bot_config={"key": "val"},
        )
        result = await service.get_device_info("bot-abc123")
        mock_plugin.get_bot.assert_awaited_once_with(bot_id="bot-abc123")

    @pytest.mark.asyncio
    async def test_converts_info_correctly(self, service, mock_plugin):
        """_BotInfo -> TeClawDeviceInfo field mapping."""
        mock_plugin.get_bot.return_value = _BotInfo(
            teclaw_bot_id="bot-abc123",
            status="ONLINE",
            teclaw_bot_config={"model": "gpt-4"},
        )
        result = await service.get_device_info("bot-abc123")
        assert isinstance(result, TeClawDeviceInfo)
        assert result.platform == "teclaw"
        assert result.teclaw_bot_id == "bot-abc123"
        assert result.status == "ONLINE"
        assert result.online_teclaw_bot_config == {"model": "gpt-4"}
        assert result.gray_teclaw_bot_config is None
        assert result.gray_strategy is None

    @pytest.mark.asyncio
    async def test_converts_unconfigured_bot_correctly(self, service, mock_plugin):
        """_BotInfo with teclaw_bot_config=None."""
        mock_plugin.get_bot.return_value = _BotInfo(
            teclaw_bot_id="bot-unconfigured",
            status="PENDING",
            teclaw_bot_config=None,
        )
        result = await service.get_device_info("bot-unconfigured")
        assert result.teclaw_bot_id == "bot-unconfigured"
        assert result.status == "PENDING"
        assert result.online_teclaw_bot_config is None


# ---------------------------------------------------------------------------
# Test resolve_invoke_http_info (delegation)
# ---------------------------------------------------------------------------


class TestResolveInvokeHttpInfo:
    @pytest.mark.asyncio
    async def test_delegates_to_plugin(self, service, mock_plugin):
        """Verify plugin.resolve_http_conn_info called with correct args."""
        mock_plugin.resolve_http_conn_info.return_value = HttpConnectionInfo(
            http_url="https://teclaw.test/api",
            token="bot-abc123",
        )
        result = await service.resolve_invoke_http_info("bot-abc123", 443, "/api")
        mock_plugin.resolve_http_conn_info.assert_awaited_once_with(
            bot_id="bot-abc123",
            port=443,
            path="/api",
            template_id=1,
        )

    @pytest.mark.asyncio
    async def test_handles_none_path(self, service, mock_plugin):
        """path=None -> path='/'."""
        mock_plugin.resolve_http_conn_info.return_value = HttpConnectionInfo(
            http_url="https://teclaw.test/",
            token="bot-abc123",
        )
        result = await service.resolve_invoke_http_info("bot-abc123", 443, None)
        mock_plugin.resolve_http_conn_info.assert_awaited_once_with(
            bot_id="bot-abc123",
            port=443,
            path="/",
            template_id=1,
        )
        assert result.token == "bot-abc123"


# ---------------------------------------------------------------------------
# Test resolve_ws_conn_info (delegation)
# ---------------------------------------------------------------------------


class TestResolveWsConnInfo:
    @pytest.mark.asyncio
    async def test_delegates_to_plugin(self, service, mock_plugin):
        """Verify plugin.resolve_ws_conn_info called."""
        mock_plugin.resolve_ws_conn_info.return_value = WsConnectionInfo(
            ws_url="wss://teclaw.test/ws",
            token="bot-abc123",
            target="teclaw.test:8443:bot-abc123",
            expires_at=None,
        )
        result = await service.resolve_ws_conn_info("bot-abc123", 8443, "/ws")
        mock_plugin.resolve_ws_conn_info.assert_awaited_once_with(
            bot_id="bot-abc123",
            port=8443,
            path="/ws",
            template_id=1,
        )


# ---------------------------------------------------------------------------
# Test close (delegation)
# ---------------------------------------------------------------------------


class TestClose:
    @pytest.mark.asyncio
    async def test_delegates_to_plugin(self, service, mock_plugin):
        """Verify plugin.close called."""
        await service.close()
        mock_plugin.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test NotImplementedError methods
# ---------------------------------------------------------------------------


class TestNotImplementedErrors:
    @pytest.mark.asyncio
    async def test_execute_command_raises(self, service):
        with pytest.raises(
            NotImplementedError, match="does not support execute_command"
        ):
            await service.execute_command("bot-123", "ls")

    @pytest.mark.asyncio
    async def test_invoke_http_in_device_raises(self, service):
        with pytest.raises(
            NotImplementedError, match="does not support HTTP invocation"
        ):
            await service.invoke_http_in_device(
                "bot-123", "GET", 8080, "/api", None, {}, b""
            )

    @pytest.mark.asyncio
    async def test_update_device_ttl_raises(self, service):
        with pytest.raises(NotImplementedError, match="does not support TTL renewal"):
            await service.update_device_ttl("bot-123")

    @pytest.mark.asyncio
    async def test_open_folder_raises(self, service):
        with pytest.raises(NotImplementedError, match="does not support open_folder"):
            await service.open_folder("bot-123", "/path/to/folder")

    @pytest.mark.asyncio
    async def test_list_instances_raises(self, service):
        with pytest.raises(
            NotImplementedError, match="does not support instance listing"
        ):
            await service.list_instances({"limit": 10})


# ---------------------------------------------------------------------------
# Test update_outbound_operation_rule
# ---------------------------------------------------------------------------


class TestUpdateOutboundOperationRule:
    @pytest.mark.asyncio
    async def test_delegates_to_plugin_with_rules(self, service, mock_plugin):
        """Verify plugin.update_outbound_rule called with correct args and returns True."""
        mock_plugin.update_outbound_rule.return_value = True
        mock_hrule1 = MagicMock()
        mock_hrule1.model_dump.return_value = {"value": "test1"}
        mock_hrule2 = MagicMock()
        mock_hrule2.model_dump.return_value = {"action": "replace"}
        mock_rule = MagicMock()
        mock_rule.header_operation_rules = [mock_hrule1, mock_hrule2]

        result = await service.update_outbound_operation_rule("bot-123", mock_rule)

        mock_plugin.update_outbound_rule.assert_awaited_once_with(
            "bot-123",
            {"header_operation_rules": [{"value": "test1"}, {"action": "replace"}]},
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_handles_empty_header_operation_rules(self, service, mock_plugin):
        """Verify empty list produces {"header_operation_rules": []}."""
        mock_plugin.update_outbound_rule.return_value = True
        mock_rule = MagicMock()
        mock_rule.header_operation_rules = []

        result = await service.update_outbound_operation_rule("bot-123", mock_rule)

        mock_plugin.update_outbound_rule.assert_awaited_once_with(
            "bot-123", {"header_operation_rules": []}
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_handles_none_header_operation_rules(self, service, mock_plugin):
        """Verify None header_operation_rules produces {"header_operation_rules": []}."""
        mock_plugin.update_outbound_rule.return_value = True
        mock_rule = MagicMock()
        mock_rule.header_operation_rules = None

        result = await service.update_outbound_operation_rule("bot-123", mock_rule)

        mock_plugin.update_outbound_rule.assert_awaited_once_with(
            "bot-123", {"header_operation_rules": []}
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_propagates_plugin_false_result(self, service, mock_plugin):
        """Verify service returns False when plugin returns False."""
        mock_plugin.update_outbound_rule.return_value = False
        mock_rule = MagicMock()
        mock_rule.header_operation_rules = [MagicMock()]
        mock_rule.header_operation_rules[0].model_dump.return_value = {"key": "val"}

        result = await service.update_outbound_operation_rule("bot-456", mock_rule)

        mock_plugin.update_outbound_rule.assert_awaited_once_with(
            "bot-456", {"header_operation_rules": [{"key": "val"}]}
        )
        assert result is False
