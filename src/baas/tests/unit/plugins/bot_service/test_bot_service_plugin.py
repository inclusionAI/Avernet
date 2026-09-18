"""Unit tests for BotServicePlugin implementations.

Covers:
- AiohttpBotServicePlugin construction and report() HTTP POST behaviour
- AiohttpBotServicePlugin get_binding() HTTP GET with error propagation
- AiohttpBotServicePlugin get_caller_connection() POST with self-minted
  Principal, need_poll bounded polling, and error mapping
- StubBotServicePlugin report() is a no-op, get_binding() returns stub data
- LocalBotServicePlugin report() logs debug, get_binding() raises PaasError
- AiohttpBotServicePlugin.report() noop when base_url is empty
- AiohttpBotServicePlugin.report() logs WARNING on HTTP error without raising
- LogRelationPayload construction and to_dict() output
- BotBindingData construction and field defaults
"""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import jwt
import pytest

from secbaas.community.api.device_manage import ErrorCode, PaasError
from secbaas.community.plugins.bot_service import (
    AiohttpBotServicePlugin,
    CallerPrincipalConfig,
    CallerPrincipalSigner,
    LocalBotServicePlugin,
    StubBotServicePlugin,
)
from secbaas.community.plugins.bot_service.real._plugin import (
    _CLAUDE_CODE_NORMAL_TEMPLATE,
    resolve_claude_code_engine,
)
from secbaas.community.spi.bot_service import BotBindingData, LogRelationPayload

# ==================== Tests: AiohttpBotServicePlugin construction ==============


class TestAiohttpBotServicePluginConstruction:
    def test_default_values(self):
        """Default: base_url="" (noop), timeout=10.0."""
        plugin = AiohttpBotServicePlugin()
        assert plugin._base_url == ""
        assert plugin._timeout == 10.0

    def test_explicit_values(self):
        """Explicit overrides for base_url and timeout."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://example.com",
            timeout=5.0,
        )
        assert plugin._base_url == "https://example.com"
        assert plugin._timeout == 5.0


# ==================== Tests: LocalBotServicePlugin ============================


class TestLocalBotServicePlugin:
    @pytest.mark.asyncio
    async def test_report_logs_debug(self):
        """LocalBotServicePlugin.report() logs payload at DEBUG."""
        plugin = LocalBotServicePlugin()
        payload = LogRelationPayload(
            biz_scene="bot_run",
            biz_task_id="task_123",
            engine="openclaw",
            collector="baas",
        )
        with patch(
            "secbaas.community.plugins.bot_service.local._local_plugin.logger"
        ) as mock_logger:
            await plugin.report(payload)
            mock_logger.debug.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        """LocalBotServicePlugin.close() is a no-op."""
        plugin = LocalBotServicePlugin()
        await plugin.close()


# ==================== Tests: StubBotServicePlugin ============================


class TestStubBotServicePlugin:
    @pytest.mark.asyncio
    async def test_report_is_noop(self):
        """StubBotServicePlugin.report() returns immediately."""
        plugin = StubBotServicePlugin()
        payload = LogRelationPayload(
            biz_scene="bot_run",
            biz_task_id="task_123",
            engine="openclaw",
            collector="baas",
        )
        # Should complete without error
        await plugin.report(payload)

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        """StubBotServicePlugin.close() is a no-op."""
        plugin = StubBotServicePlugin()
        await plugin.close()


# ==================== Tests: LogRelationPayload.to_dict ======================


class TestLogRelationPayloadToDict:
    def test_payload_to_dict(self):
        """LogRelationPayload.to_dict() produces camelCase keys matching the API spec."""
        payload = LogRelationPayload(
            biz_scene="bot_run",
            biz_task_id="task_123",
            engine="openclaw",
            collector="secbaas",
            refs=[{"ref_type": "trace", "ref_value": "t_456"}],
            user_id="u_001",
            bot_id="b_001",
        )
        d = payload.to_dict()
        assert d == {
            "biz_scene": "bot_run",
            "biz_task_id": "task_123",
            "engine": "openclaw",
            "collector": "secbaas",
            "refs": [{"ref_type": "trace", "ref_value": "t_456"}],
            "user_id": "u_001",
            "bot_id": "b_001",
        }

    def test_payload_defaults(self):
        """LogRelationPayload default fields: refs=[], user_id="", bot_id=""."""
        payload = LogRelationPayload(
            biz_scene="bot_run",
            biz_task_id="task_123",
            engine="openclaw",
            collector="secbaas",
        )
        assert payload.refs == []
        assert payload.user_id == ""
        assert payload.bot_id == ""
        d = payload.to_dict()
        assert d["refs"] == []
        assert d["user_id"] == ""
        assert d["bot_id"] == ""


# ==================== Tests: BotBindingData ==================================


class TestBotBindingData:
    def test_construction_with_all_fields(self):
        """BotBindingData constructed with all fields."""
        data = BotBindingData(
            bot_id="bot_001",
            owner_id="owner_001",
            bot_type="service",
            engine_type="openclaw",
            publish_id=9527,
            publish_status="success",
            binding_id=202,
            device_provider="baas",
            device_id="BOT-82e5aab5637941b689e733bd35dd982f",
        )
        assert data.bot_id == "bot_001"
        assert data.owner_id == "owner_001"
        assert data.bot_type == "service"
        assert data.engine_type == "openclaw"
        assert data.publish_id == 9527
        assert data.publish_status == "success"
        assert data.binding_id == 202
        assert data.device_provider == "baas"
        assert data.device_id == "BOT-82e5aab5637941b689e733bd35dd982f"

    def test_defaults(self):
        """BotBindingData default fields."""
        data = BotBindingData(
            bot_id="bot_001",
            owner_id="owner_001",
            bot_type="personal",
            engine_type="openclaw",
        )
        assert data.publish_id is None
        assert data.publish_status is None
        assert data.binding_id == 0
        assert data.device_provider == ""
        assert data.device_id == ""


# ==================== Tests: AiohttpBotServicePlugin.report ===================


class TestAiohttpBotServicePluginReport:
    @pytest.mark.asyncio
    async def test_report_sends_http_post(self):
        """AiohttpBotServicePlugin.report() sends a POST to the correct URL."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://log-relations.example.com",
        )

        payload = LogRelationPayload(
            biz_scene="bot_run",
            biz_task_id="task_123",
            engine="openclaw",
            collector="baas",
            refs=[{"ref_type": "session_key", "ref_value": "sess-001"}],
            user_id="u_001",
            bot_id="b_001",
        )

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value="ok")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        await plugin.report(payload)

        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        assert call_args[0][0] == (
            "https://log-relations.example.com/api/bot-chat/log-relations"
        )
        assert call_args[1]["json"] == payload.to_dict()

        await plugin.close()

    @pytest.mark.asyncio
    async def test_report_empty_base_url_is_noop(self):
        """When base_url is empty, report() returns immediately without sending."""
        plugin = AiohttpBotServicePlugin(base_url="")

        payload = LogRelationPayload(
            biz_scene="bot_run",
            biz_task_id="task_123",
            engine="openclaw",
            collector="baas",
        )

        await plugin.report(payload)
        assert plugin._session is None

    @pytest.mark.asyncio
    async def test_report_http_error_logs_warning(self):
        """HTTP response status >= 400 → WARNING logged, no raise."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://log-relations.example.com",
        )

        payload = LogRelationPayload(
            biz_scene="bot_run",
            biz_task_id="task_123",
            engine="openclaw",
            collector="baas",
        )

        mock_response = MagicMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        with patch(
            "secbaas.community.plugins.bot_service.real._plugin.logger"
        ) as mock_logger:
            await plugin.report(payload)
            mock_logger.warning.assert_called_once()

        await plugin.close()

    @pytest.mark.asyncio
    async def test_report_client_error_logs_warning(self):
        """aiohttp.ClientError → WARNING logged, no raise."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://log-relations.example.com",
        )

        payload = LogRelationPayload(
            biz_scene="bot_run",
            biz_task_id="task_123",
            engine="openclaw",
            collector="baas",
        )

        mock_session = MagicMock()
        mock_session.post = MagicMock(
            side_effect=aiohttp.ClientError("connection refused")
        )
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        with patch(
            "secbaas.community.plugins.bot_service.real._plugin.logger"
        ) as mock_logger:
            await plugin.report(payload)
            mock_logger.warning.assert_called_once()

        await plugin.close()

    @pytest.mark.asyncio
    async def test_report_timeout_error_logs_warning(self):
        """TimeoutError → WARNING logged, no raise."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://log-relations.example.com",
        )

        payload = LogRelationPayload(
            biz_scene="bot_run",
            biz_task_id="task_123",
            engine="openclaw",
            collector="baas",
        )

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=TimeoutError("timed out"))
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        with patch(
            "secbaas.community.plugins.bot_service.real._plugin.logger"
        ) as mock_logger:
            await plugin.report(payload)
            mock_logger.warning.assert_called_once()

        await plugin.close()


# ==================== Tests: AiohttpBotServicePlugin.get_binding ==============


class TestAiohttpBotServicePluginGetBinding:
    @pytest.mark.asyncio
    async def test_get_binding_sends_http_get(self):
        """get_binding() sends GET to the correct URL and returns BotBindingData."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        api_response = {
            "success": True,
            "message": "查询成功",
            "error_code": None,
            "data": {
                "bot_id": "bot_service_001",
                "owner_id": "20881234",
                "bot_type": "service",
                "engine_type": "openclaw",
                "publish_id": 9527,
                "publish_status": "success",
                "binding_id": 202,
                "device_provider": "baas",
                "device_id": "BOT-82e5aab5637941b689e733bd35dd982f",
            },
        }

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=api_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        result = await plugin.get_binding("bot_service_001", "20881234", "online")

        assert isinstance(result, BotBindingData)
        assert result.bot_id == "bot_service_001"
        assert result.owner_id == "20881234"
        assert result.bot_type == "service"
        assert result.engine_type == "openclaw"
        assert result.publish_id == 9527
        assert result.publish_status == "success"
        assert result.binding_id == 202
        assert result.device_provider == "baas"
        assert result.device_id == "BOT-82e5aab5637941b689e733bd35dd982f"

        mock_session.get.assert_called_once()
        call_args = mock_session.get.call_args
        assert "/api/service-bot/publish/bot_service_001/binding" in call_args[0][0]
        assert call_args[1]["params"] == {
            "owner_id": "20881234",
            "stage": "online",
        }

        await plugin.close()

    @pytest.mark.asyncio
    async def test_get_binding_with_default_tag_appends_query_param(self):
        """get_binding(stage="eval", default_tag="default") 时 HTTP params 包含 default_tag。"""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        api_response = {
            "success": True,
            "message": "查询成功",
            "error_code": None,
            "data": {
                "bot_id": "bot_001",
                "owner_id": "owner_001",
                "bot_type": "service",
                "engine_type": "openclaw",
                "binding_id": 202,
                "device_provider": "baas",
                "device_id": "device-001",
            },
        }

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=api_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        result = await plugin.get_binding(
            "bot_001", "owner_001", "eval", default_tag="default"
        )

        assert result.binding_id == 202

        mock_session.get.assert_called_once()
        call_args = mock_session.get.call_args
        assert call_args[1]["params"] == {
            "owner_id": "owner_001",
            "stage": "eval",
            "default_tag": "default",
        }

        await plugin.close()

    @pytest.mark.asyncio
    async def test_get_binding_without_default_tag_omits_query_param(self):
        """get_binding(stage="eval") 不传 default_tag 时 HTTP params 不含 default_tag。"""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        api_response = {
            "success": True,
            "message": "查询成功",
            "error_code": None,
            "data": {
                "bot_id": "bot_001",
                "owner_id": "owner_001",
                "bot_type": "service",
                "engine_type": "openclaw",
                "binding_id": 202,
                "device_provider": "baas",
                "device_id": "device-001",
            },
        }

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=api_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        result = await plugin.get_binding("bot_001", "owner_001", "eval")

        mock_session.get.assert_called_once()
        call_args = mock_session.get.call_args
        assert "default_tag" not in call_args[1]["params"]

        await plugin.close()

    @pytest.mark.asyncio
    async def test_get_binding_all_with_default_tag_passes_through(self):
        """stage="all" + default_tag 时 default_tag 仍透传到 _get_binding_raw。"""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        inner = BotBindingData(
            bot_id="bot_001",
            owner_id="owner_001",
            bot_type="service",
            engine_type="openclaw",
            binding_id=202,
            device_provider="baas",
            device_id="device-001",
        )

        plugin._get_binding_raw = AsyncMock(return_value=inner)

        result = await plugin.get_binding(
            "bot_001", "owner_001", "all", default_tag="default"
        )

        assert result.binding_id == 202
        # _get_binding_raw 应被调用且 default_tag 透传
        plugin._get_binding_raw.assert_called_once_with(
            "bot_001", "owner_001", "online", default_tag="default"
        )

    @pytest.mark.asyncio
    async def test_get_binding_empty_base_url_raises_paas_error(self):
        """Empty base_url → PaasError(CONFIG_INVALID)."""
        plugin = AiohttpBotServicePlugin(base_url="")

        with pytest.raises(PaasError) as exc_info:
            await plugin.get_binding("bot_001", "owner_001", "online")

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID

    @pytest.mark.asyncio
    async def test_get_binding_http_500_raises_platform_error(self):
        """HTTP 500 → PaasError(PLATFORM_ERROR)."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        mock_response = MagicMock()
        mock_response.status = 500
        mock_response.json = AsyncMock(
            return_value={"message": "Internal Server Error"}
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        with pytest.raises(PaasError) as exc_info:
            await plugin.get_binding("bot_001", "owner_001", "online")

        assert exc_info.value.code == ErrorCode.PLATFORM_ERROR

    @pytest.mark.asyncio
    async def test_get_binding_http_401_raises_auth_failed(self):
        """HTTP 401 → PaasError(AUTH_FAILED)."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        mock_response = MagicMock()
        mock_response.status = 401
        mock_response.json = AsyncMock(return_value={"message": "Unauthorized"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        with pytest.raises(PaasError) as exc_info:
            await plugin.get_binding("bot_001", "owner_001", "online")

        assert exc_info.value.code == ErrorCode.AUTH_FAILED

    @pytest.mark.asyncio
    async def test_get_binding_http_429_raises_rate_limited(self):
        """HTTP 429 → PaasError(RATE_LIMITED)."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        mock_response = MagicMock()
        mock_response.status = 429
        mock_response.json = AsyncMock(return_value={"message": "Too Many Requests"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        with pytest.raises(PaasError) as exc_info:
            await plugin.get_binding("bot_001", "owner_001", "online")

        assert exc_info.value.code == ErrorCode.RATE_LIMITED

    @pytest.mark.asyncio
    async def test_get_binding_client_error_raises_platform_unavailable(self):
        """aiohttp.ClientError → PaasError(PLATFORM_UNAVAILABLE)."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            side_effect=aiohttp.ClientError("connection refused")
        )
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        with pytest.raises(PaasError) as exc_info:
            await plugin.get_binding("bot_001", "owner_001", "online")

        assert exc_info.value.code == ErrorCode.PLATFORM_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_get_binding_retries_transient_timeout(self):
        """A transient timeout is retried once before a successful response."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        api_response = {
            "success": True,
            "message": "查询成功",
            "error_code": None,
            "data": {
                "bot_id": "bot_001",
                "owner_id": "owner_001",
                "bot_type": "service",
                "engine_type": "openclaw",
                "binding_id": 202,
                "device_provider": "baas",
                "device_id": "device-001",
            },
        }
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=api_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            side_effect=[TimeoutError("timed out"), mock_response]
        )
        mock_session.closed = False
        mock_session.close = AsyncMock()
        plugin._session = mock_session

        with patch(
            "secbaas.community.plugins.bot_service.real._plugin.asyncio.sleep",
            new=AsyncMock(),
        ) as mock_sleep:
            result = await plugin.get_binding("bot_001", "owner_001", "online")

        assert result.binding_id == 202
        assert mock_session.get.call_count == 2
        mock_sleep.assert_awaited_once()
        await plugin.close()

    @pytest.mark.asyncio
    async def test_get_binding_timeout_raises_platform_unavailable(self):
        """TimeoutError → PaasError(PLATFORM_UNAVAILABLE)."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=TimeoutError("timed out"))
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        with pytest.raises(PaasError) as exc_info:
            await plugin.get_binding("bot_001", "owner_001", "online")

        assert exc_info.value.code == ErrorCode.PLATFORM_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_get_binding_envelope_failure_raises_paas_error(self):
        """success=false envelope with non-404 error → PaasError(PLATFORM_ERROR)."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        api_response = {
            "success": False,
            "message": "Internal server error",
            "error_code": 500,
            "data": None,
        }

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=api_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        with pytest.raises(PaasError) as exc_info:
            await plugin.get_binding("bot_001", "owner_001", "online")

        assert exc_info.value.code == ErrorCode.PLATFORM_ERROR
        assert "500" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_binding_envelope_not_found_by_error_code(self):
        """success=false, error_code=404 → PaasError(NOT_FOUND)."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        api_response = {
            "success": False,
            "message": "Bot not found",
            "error_code": 404,
            "data": None,
        }

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=api_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        with pytest.raises(PaasError) as exc_info:
            await plugin.get_binding("bot_001", "owner_001", "online")

        assert exc_info.value.code == ErrorCode.NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_binding_envelope_not_found_by_message(self):
        """success=false, message contains 'No success publish found' → PaasError(NOT_FOUND)."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        api_response = {
            "success": False,
            "message": "No success publish found for service bot",
            "error_code": 500,
            "data": None,
        }

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=api_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        with pytest.raises(PaasError) as exc_info:
            await plugin.get_binding("bot_001", "owner_001", "online")

        assert exc_info.value.code == ErrorCode.NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_binding_missing_data_raises_paas_error(self):
        """success=true but data=null → PaasError(PLATFORM_ERROR)."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        api_response = {
            "success": True,
            "message": "ok",
            "error_code": None,
            "data": None,
        }

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=api_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        with pytest.raises(PaasError) as exc_info:
            await plugin.get_binding("bot_001", "owner_001", "online")

        assert exc_info.value.code == ErrorCode.PLATFORM_ERROR
        assert "missing data" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_get_binding_envelope_shape_violation_raises_platform_error(self):
        """success=true but data breaks the BotBindingData contract → PaasError(PLATFORM_ERROR)."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        api_response = {
            "success": True,
            "message": "ok",
            "error_code": 0,
            "data": {"bot_id": "bot_001"},
        }

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=api_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        with pytest.raises(PaasError) as exc_info:
            await plugin.get_binding("bot_001", "owner_001", "online")

        assert exc_info.value.code == ErrorCode.PLATFORM_ERROR
        mock_session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_binding_personal_bot_response(self):
        """get_binding() works for personal bot (no publish_id/publish_status)."""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        api_response = {
            "success": True,
            "message": "查询成功",
            "error_code": None,
            "data": {
                "bot_id": "bot_personal_001",
                "owner_id": "20881234",
                "bot_type": "personal",
                "engine_type": "openclaw",
                "publish_id": None,
                "publish_status": None,
                "binding_id": 101,
                "device_provider": "arca",
                "device_id": "ARCA-SANDBOX-285061ae",
            },
        }

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=api_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.closed = False
        mock_session.close = AsyncMock()

        plugin._session = mock_session

        result = await plugin.get_binding("bot_personal_001", "20881234", "online")

        assert result.bot_type == "personal"
        assert result.publish_id is None
        assert result.publish_status is None
        assert result.binding_id == 101
        assert result.device_provider == "arca"

    # ── stage == "all" 多阶段查询 ──

    @pytest.mark.asyncio
    async def test_get_binding_all_returns_first_success(self):
        """stage="all": online 命中时直接返回，不查 verify/draft。"""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        inner = BotBindingData(
            bot_id="bot_001",
            owner_id="20881234",
            bot_type="service",
            engine_type="openclaw",
            publish_id=1,
            publish_status="success",
            binding_id=202,
            device_provider="baas",
            device_id="BOT-abc",
        )

        plugin._get_binding_raw = AsyncMock(return_value=inner)

        result = await plugin.get_binding("bot_001", "20881234", "all")

        assert result.binding_id == 202
        plugin._get_binding_raw.assert_called_once_with(
            "bot_001", "20881234", "online", default_tag=None
        )

    @pytest.mark.asyncio
    async def test_get_binding_all_falls_through_to_verify(self):
        """stage="all": online 返回 NOT_FOUND，verify 命中。"""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        verify_inner = BotBindingData(
            bot_id="bot_001",
            owner_id="20881234",
            bot_type="service",
            engine_type="openclaw",
            publish_id=2,
            publish_status="validating",
            binding_id=303,
            device_provider="baas",
            device_id="BOT-xyz",
        )

        plugin._get_binding_raw = AsyncMock(
            side_effect=[
                PaasError(ErrorCode.NOT_FOUND, "not found"),
                verify_inner,
            ]
        )

        result = await plugin.get_binding("bot_001", "20881234", "all")

        assert result.binding_id == 303
        assert result.publish_status == "validating"
        assert plugin._get_binding_raw.call_count == 2

    @pytest.mark.asyncio
    async def test_get_binding_all_falls_through_to_draft(self):
        """stage="all": online 和 verify 都 NOT_FOUND，draft 命中。"""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        draft_inner = BotBindingData(
            bot_id="bot_001",
            owner_id="20881234",
            bot_type="service",
            engine_type="openclaw",
            publish_id=None,
            publish_status=None,
            binding_id=404,
            device_provider="baas",
            device_id="BOT-draft",
        )

        plugin._get_binding_raw = AsyncMock(
            side_effect=[
                PaasError(ErrorCode.NOT_FOUND, "not found"),
                PaasError(ErrorCode.NOT_FOUND, "not found"),
                draft_inner,
            ]
        )

        result = await plugin.get_binding("bot_001", "20881234", "all")

        assert result.binding_id == 404
        assert plugin._get_binding_raw.call_count == 3

    @pytest.mark.asyncio
    async def test_get_binding_all_all_stages_not_found_raises(self):
        """stage="all": 全部 stage 都 NOT_FOUND → raise 最后的 NOT_FOUND。"""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        plugin._get_binding_raw = AsyncMock(
            side_effect=[
                PaasError(ErrorCode.NOT_FOUND, "online not found"),
                PaasError(ErrorCode.NOT_FOUND, "verify not found"),
                PaasError(ErrorCode.NOT_FOUND, "draft not found"),
            ]
        )

        with pytest.raises(PaasError) as exc_info:
            await plugin.get_binding("bot_001", "20881234", "all")

        assert exc_info.value.code == ErrorCode.NOT_FOUND
        assert "draft not found" in str(exc_info.value)
        assert plugin._get_binding_raw.call_count == 3

    @pytest.mark.asyncio
    async def test_get_binding_all_non_not_found_error_propagates(self):
        """stage="all": 非 NOT_FOUND 错误（如 AUTH_FAILED）直接 raise，不继续。"""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        plugin._get_binding_raw = AsyncMock(
            side_effect=PaasError(ErrorCode.AUTH_FAILED, "unauthorized")
        )

        with pytest.raises(PaasError) as exc_info:
            await plugin.get_binding("bot_001", "20881234", "all")

        assert exc_info.value.code == ErrorCode.AUTH_FAILED
        plugin._get_binding_raw.assert_called_once()


# ==================== Tests: LocalBotServicePlugin.get_binding ================


class TestLocalBotServicePluginGetBinding:
    @pytest.mark.asyncio
    async def test_get_binding_raises_platform_unavailable(self):
        """LocalBotServicePlugin.get_binding() raises PaasError(PLATFORM_UNAVAILABLE)."""
        plugin = LocalBotServicePlugin()

        with pytest.raises(PaasError) as exc_info:
            await plugin.get_binding("bot_001", "owner_001", "online")

        assert exc_info.value.code == ErrorCode.PLATFORM_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_get_binding_with_default_tag_raises_platform_unavailable(self):
        """LocalBotServicePlugin.get_binding() with default_tag 仍抛 PaasError。"""
        plugin = LocalBotServicePlugin()

        with pytest.raises(PaasError) as exc_info:
            await plugin.get_binding(
                "bot_001", "owner_001", "eval", default_tag="default"
            )

        assert exc_info.value.code == ErrorCode.PLATFORM_UNAVAILABLE


# ==================== Tests: StubBotServicePlugin.get_binding =================


class TestStubBotServicePluginGetBinding:
    @pytest.mark.asyncio
    async def test_get_binding_returns_stub_data(self):
        """StubBotServicePlugin.get_binding() returns deterministic stub data."""
        plugin = StubBotServicePlugin()

        result = await plugin.get_binding("bot_001", "owner_001", "online")

        assert isinstance(result, BotBindingData)
        assert result.bot_id == "bot_001"
        assert result.owner_id == "owner_001"
        assert result.bot_type == "service"
        assert result.engine_type == "openclaw"
        assert result.publish_id is None
        assert result.publish_status is None
        assert result.binding_id == 0
        assert result.device_provider == "stub"
        assert result.device_id == "stub-device"

    @pytest.mark.asyncio
    async def test_get_binding_error_env_var(self, monkeypatch):
        monkeypatch.setenv("BAAS_STUB_BOT_BINDING_ERROR", "1")
        plugin = StubBotServicePlugin()

        with pytest.raises(AttributeError, match="PAAS_ERROR"):
            await plugin.get_binding("bot_001", "owner_001", "online")

    @pytest.mark.asyncio
    async def test_get_binding_not_found_env_var(self, monkeypatch):
        monkeypatch.setenv("BAAS_STUB_BOT_BINDING_NOT_FOUND", "1")
        plugin = StubBotServicePlugin()

        result = await plugin.get_binding("bot_001", "owner_001", "online")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_binding_with_default_tag_returns_stub_data(self):
        """StubBotServicePlugin.get_binding() with default_tag 返回 stub 数据（签名兼容）。"""
        plugin = StubBotServicePlugin()

        result = await plugin.get_binding(
            "bot_001", "owner_001", "eval", default_tag="default"
        )

        assert isinstance(result, BotBindingData)
        assert result.bot_id == "bot_001"
        assert result.device_provider == "stub"


class TestAiohttpBotServicePluginRuntimeEngineSelection:
    """Runtime engine is selected only at the Backend HTTP consumption boundary."""

    @staticmethod
    def _binding_inner(**overrides) -> BotBindingData:
        fields = {
            "bot_id": "bot_personal_001",
            "owner_id": "20881234",
            "bot_type": "personal",
            "engine_type": "claude_code",
            "template_type": "normalCC",
            "binding_id": 101,
            "device_provider": "arca",
            "device_id": "ARCA-SANDBOX-001",
        }
        fields.update(overrides)
        return BotBindingData(**fields)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "runtime_engine_type",
        [
            "openclaw",
            "teclaw",
            "aicoding",
            "hermes",
            "claude_code",
            "deepseek_harness",
        ],
    )
    async def test_supported_runtime_engine_overrides_original_engine(
        self, runtime_engine_type
    ):
        plugin = AiohttpBotServicePlugin(base_url="https://agentclaw.example.com")
        plugin._get_binding_raw = AsyncMock(
            return_value=self._binding_inner(
                active_runtime_engine_type=f"  {runtime_engine_type}  "
            )
        )

        result = await plugin.get_binding("bot_personal_001", "20881234", "online")

        assert result.engine_type == runtime_engine_type

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("runtime_engine_type", "bot_type", "warning_expected"),
        [
            # None 与字段缺失同义：类型化信封不区分缺省与显式 null，均不告警
            (None, "personal", False),
            ("", "personal", True),
            ("   ", "personal", True),
            (123, "personal", True),
            ("unsupported", "personal", True),
            ("unsupported", "service", True),
            ("", "service", False),
        ],
    )
    async def test_invalid_runtime_engine_falls_back_to_original_engine(
        self, runtime_engine_type, bot_type, warning_expected
    ):
        plugin = AiohttpBotServicePlugin(base_url="https://agentclaw.example.com")
        plugin._get_binding_raw = AsyncMock(
            return_value=self._binding_inner(
                bot_type=bot_type,
                active_runtime_engine_type=runtime_engine_type,
            )
        )

        with patch(
            "secbaas.community.plugins.bot_service.real._plugin.logger"
        ) as mock_logger:
            result = await plugin.get_binding("bot_personal_001", "20881234", "online")

        assert result.engine_type == "claude_code"
        if warning_expected:
            mock_logger.warning.assert_called_once()
        else:
            mock_logger.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_runtime_engine_field_keeps_old_backend_behavior(self):
        plugin = AiohttpBotServicePlugin(base_url="https://agentclaw.example.com")
        plugin._get_binding_raw = AsyncMock(return_value=self._binding_inner())

        with patch(
            "secbaas.community.plugins.bot_service.real._plugin.logger"
        ) as mock_logger:
            result = await plugin.get_binding("bot_personal_001", "20881234", "online")

        assert result.engine_type == "claude_code"
        mock_logger.warning.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("template_type", ["generCC", "  aicoding-default  "])
    async def test_claude_code_with_non_normal_template_routes_to_aicoding(
        self, template_type
    ):
        plugin = AiohttpBotServicePlugin(base_url="https://agentclaw.example.com")
        plugin._get_binding_raw = AsyncMock(
            return_value=self._binding_inner(template_type=template_type)
        )

        result = await plugin.get_binding("bot_personal_001", "20881234", "online")

        assert result.engine_type == "aicoding"

    @pytest.mark.asyncio
    async def test_claude_code_original_with_normal_template_stays_claude_code(self):
        plugin = AiohttpBotServicePlugin(base_url="https://agentclaw.example.com")
        plugin._get_binding_raw = AsyncMock(
            return_value=self._binding_inner(template_type="normalCC")
        )

        result = await plugin.get_binding("bot_personal_001", "20881234", "online")

        assert result.engine_type == "claude_code"


class TestResolveClaudeCodeEngine:
    """Routing of a claude_code active engine by template_type."""

    def test_claude_code_with_non_normal_template_routes_to_aicoding(self):
        for template_type in ("generCC", "  aicoding-default  ", "other"):
            assert (
                resolve_claude_code_engine("claude_code", template_type) == "aicoding"
            )

    @pytest.mark.parametrize(
        "template_type", [None, "", "   ", _CLAUDE_CODE_NORMAL_TEMPLATE]
    )
    def test_claude_code_with_empty_or_normal_template_stays_claude_code(
        self, template_type
    ):
        assert resolve_claude_code_engine("claude_code", template_type) == "claude_code"

    def test_claude_code_normal_template_is_whitespace_insensitive(self):
        assert resolve_claude_code_engine("claude_code", " normalCC ") == "claude_code"

    @pytest.mark.parametrize(
        "active_engine", ["openclaw", "teclaw", "aicoding", "hermes"]
    )
    def test_non_claude_code_engine_unchanged_regardless_of_template(
        self, active_engine
    ):
        for template_type in (None, "", "normalCC", "generCC", "  x  ", 123):
            assert (
                resolve_claude_code_engine(active_engine, template_type)
                == active_engine
            )


# ==================== Tests: AiohttpBotServicePlugin.get_caller_connection =====


def _mock_caller_response(body: dict, status: int = 200) -> MagicMock:
    mock_response = MagicMock()
    mock_response.status = status
    mock_response.json = AsyncMock(return_value=body)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    return mock_response


def _caller_ready_body(sandbox_id: str) -> dict:
    return {
        "success": True,
        "message": "获取成功",
        "error_code": 0,
        "data": {
            "instance": {
                "user_id": "u_001",
                "bot_id": "bot_001",
                "owner_id": "20881234",
                "status": "success",
                "ext": {"bot_uuid": "BOT-caller-uuid", "version": 1},
            },
            "connection": {
                "ws_url": "wss://connection.example.invalid/path",
                "token": "<connection-token>",
                "target": sandbox_id,
                "paas_device_id": "<paas-device-id>",
                "baas_base_url": "https://baas.example.invalid",
                "engine_port": 20003,
                "tenant": "tenant-1",
                "bot_uuid": "BOT-caller-uuid",
            },
            "need_poll": False,
        },
    }


def _caller_poll_body() -> dict:
    return {
        "success": True,
        "message": "获取成功",
        "error_code": 0,
        "data": {
            "instance": {"status": "UPGRADING"},
            "connection": None,
            "need_poll": True,
        },
    }


_CALLER_PRINCIPAL_SECRET_NAME = "other_manual_teamclawgw_principal_signing_key"
_CALLER_PRINCIPAL_KEY = "unit-test-principal-signing-key-0123456789abcdef"
_CALLER_COOKIE = "IAM_TOKEN=iam-value; session=abc"


class _FakeSecretStore:
    def get_secret(self, secret_name: str) -> str:
        if secret_name != _CALLER_PRINCIPAL_SECRET_NAME:
            raise RuntimeError(f"Secret not found: {secret_name}")
        return _CALLER_PRINCIPAL_KEY


def _caller_principal_signer() -> CallerPrincipalSigner:
    return CallerPrincipalSigner(
        secret_store=_FakeSecretStore(),
        config=CallerPrincipalConfig(),
    )


class TestAiohttpBotServicePluginGetCallerConnection:
    """POST app-caller-connection + GET /api/v1/token/iam (backend contracts)."""

    _MODULE = "secbaas.community.plugins.bot_service.real._plugin"

    def _make_plugin(self, post, get=None) -> AiohttpBotServicePlugin:
        session = MagicMock()
        session.closed = False
        session.close = AsyncMock()
        session.post = post
        if get is None:
            # 就绪后的 IAM 刷新默认成功（回显型响应，iam_token 不被消费）
            get = MagicMock(
                return_value=_mock_caller_response(
                    {"success": True, "iam_token": "iam-echo"}
                )
            )
        session.get = get
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
            principal_signer=_caller_principal_signer(),
        )
        plugin._session = session
        return plugin

    async def _call(self, plugin: AiohttpBotServicePlugin) -> str:
        return await plugin.get_caller_connection(
            bot_id="bot_001",
            owner_id="20881234",
            user_id="u_001",
            cookie=_CALLER_COOKIE,
        )

    @pytest.mark.asyncio
    async def test_success_returns_target(self):
        """Ready response returns connection.target; query params,
        empty body, self-minted Principal header and the follow-up IAM
        refresh call all match the contract."""
        mock_post = MagicMock(
            return_value=_mock_caller_response(_caller_ready_body("sbx-123"))
        )
        mock_get = MagicMock(
            return_value=_mock_caller_response(
                {"success": True, "iam_token": "iam-echo"}
            )
        )
        plugin = self._make_plugin(mock_post, mock_get)

        result = await self._call(plugin)

        assert result == "sbx-123"

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == (
            "https://agentclaw.example.com/api/v1/expert-chats/app-caller-connection"
        )
        assert call_args[1]["params"] == {
            "bot_id": "bot_001",
            "owner_id": "20881234",
            "user_id": "u_001",
            "force_upgrade": "false",
        }
        assert "json" not in call_args[1]
        headers = call_args[1]["headers"]
        # 自签 principal：HS256 + iss/aud 值校验 + 必填 claims，不带 principals
        claims = jwt.decode(
            headers["X-Avernet-Principal"],
            _CALLER_PRINCIPAL_KEY,
            algorithms=["HS256"],
            issuer="gateway",
            audience="backend",
            options={"require": ["exp", "iat", "iss"]},
        )
        assert "principals" not in claims
        assert "X-Request-ID" in headers

        # 就绪后单次 IAM 刷新：Cookie 身份 + bot_id query，不带 Principal
        mock_get.assert_called_once()
        get_args = mock_get.call_args
        assert get_args[0][0] == "https://agentclaw.example.com/api/v1/token/iam"
        assert get_args[1]["params"] == {
            "bot_id": "bot_001",
            "is_test_exchange": "false",
        }
        assert get_args[1]["headers"]["Cookie"] == _CALLER_COOKIE

        await plugin.close()

    @pytest.mark.asyncio
    async def test_need_poll_then_success(self, monkeypatch):
        """need_poll=true is re-queried with force_upgrade=false until ready."""
        monkeypatch.setattr(f"{self._MODULE}._CALLER_POLL_INITIAL_DELAY_SECONDS", 0.0)
        mock_post = MagicMock(
            side_effect=[
                _mock_caller_response(_caller_poll_body()),
                _mock_caller_response(_caller_ready_body("sbx-after-poll")),
            ]
        )
        plugin = self._make_plugin(mock_post)

        result = await self._call(plugin)

        assert result == "sbx-after-poll"
        assert mock_post.call_count == 2
        for call in mock_post.call_args_list:
            assert call[1]["params"]["force_upgrade"] == "false"

        await plugin.close()

    @pytest.mark.asyncio
    async def test_need_poll_deadline_raises_device_not_ready(self, monkeypatch):
        """Instance still not ready at the poll deadline → DEVICE_NOT_READY."""
        monkeypatch.setattr(f"{self._MODULE}._CALLER_POLL_INITIAL_DELAY_SECONDS", 0.02)
        monkeypatch.setattr(f"{self._MODULE}._CALLER_POLL_MAX_DELAY_SECONDS", 0.02)
        monkeypatch.setattr(f"{self._MODULE}._CALLER_POLL_DEADLINE_SECONDS", 0.05)
        mock_post = MagicMock(return_value=_mock_caller_response(_caller_poll_body()))
        plugin = self._make_plugin(mock_post)

        with pytest.raises(PaasError) as exc_info:
            await self._call(plugin)

        assert exc_info.value.code == ErrorCode.DEVICE_NOT_READY
        assert mock_post.call_count >= 2

        await plugin.close()

    @pytest.mark.asyncio
    async def test_business_403_raises_not_found(self):
        """success=false with error_code=403 → NOT_FOUND (no polling)."""
        body = {
            "success": False,
            "message": "无权限执行此操作",
            "error_code": 403,
            "data": None,
        }
        mock_post = MagicMock(return_value=_mock_caller_response(body))
        plugin = self._make_plugin(mock_post)

        with pytest.raises(PaasError) as exc_info:
            await self._call(plugin)

        assert exc_info.value.code == ErrorCode.NOT_FOUND
        mock_post.assert_called_once()

        await plugin.close()

    @pytest.mark.asyncio
    async def test_business_5999_raises_platform_error(self):
        """success=false with error_code=5999 → PLATFORM_ERROR."""
        body = {
            "success": False,
            "message": "获取 Caller 连接失败，请稍后重试",
            "error_code": 5999,
            "data": None,
        }
        mock_post = MagicMock(return_value=_mock_caller_response(body))
        plugin = self._make_plugin(mock_post)

        with pytest.raises(PaasError) as exc_info:
            await self._call(plugin)

        assert exc_info.value.code == ErrorCode.PLATFORM_ERROR

        await plugin.close()

    @pytest.mark.asyncio
    async def test_http_401_raises_auth_failed_without_retry(self):
        """HTTP 401 (Principal rejected) fails fast — no polling."""
        mock_post = MagicMock(
            return_value=_mock_caller_response({"detail": "Unauthorized"}, status=401)
        )
        plugin = self._make_plugin(mock_post)

        with pytest.raises(PaasError) as exc_info:
            await self._call(plugin)

        assert exc_info.value.code == ErrorCode.AUTH_FAILED
        mock_post.assert_called_once()

        await plugin.close()

    @pytest.mark.asyncio
    async def test_empty_base_url_raises_config_invalid(self):
        plugin = AiohttpBotServicePlugin(base_url="")

        with pytest.raises(PaasError) as exc_info:
            await self._call(plugin)

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID

    @pytest.mark.asyncio
    async def test_missing_signer_raises_config_invalid(self):
        """未注入 principal signer 时 fail closed，不进入 HTTP/轮询。"""
        plugin = AiohttpBotServicePlugin(base_url="https://agentclaw.example.com")

        with pytest.raises(PaasError) as exc_info:
            await self._call(plugin)

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID

    @pytest.mark.asyncio
    async def test_empty_cookie_raises_config_invalid(self):
        """空 cookie（metadata 无 cookie）本地 fail closed，不发任何请求。"""
        mock_post = MagicMock()
        mock_get = MagicMock()
        plugin = self._make_plugin(mock_post, mock_get)

        with pytest.raises(PaasError) as exc_info:
            await plugin.get_caller_connection(
                bot_id="bot_001",
                owner_id="20881234",
                user_id="u_001",
                cookie="",
            )

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID
        mock_post.assert_not_called()
        mock_get.assert_not_called()

        await plugin.close()

    @pytest.mark.asyncio
    async def test_iam_refresh_http_error_fails_after_ready(self):
        """IAM 刷新 HTTP 400（如缺 IAM_TOKEN cookie）→ CONFIG_INVALID，不重试。"""
        mock_post = MagicMock(
            return_value=_mock_caller_response(_caller_ready_body("sbx-123"))
        )
        mock_get = MagicMock(
            return_value=_mock_caller_response(
                {"success": False, "error": "IAM_TOKEN cookie not found"},
                status=400,
            )
        )
        plugin = self._make_plugin(mock_post, mock_get)

        with pytest.raises(PaasError) as exc_info:
            await self._call(plugin)

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID
        mock_get.assert_called_once()

        await plugin.close()

    @pytest.mark.asyncio
    async def test_iam_refresh_transport_error_raises_platform_unavailable(self):
        """IAM 刷新传输错误单次失败（契约：带 bot_id 的请求不自动重试）。"""
        mock_post = MagicMock(
            return_value=_mock_caller_response(_caller_ready_body("sbx-123"))
        )
        mock_get = MagicMock(side_effect=aiohttp.ClientError("iam backend down"))
        plugin = self._make_plugin(mock_post, mock_get)

        with pytest.raises(PaasError) as exc_info:
            await self._call(plugin)

        assert exc_info.value.code == ErrorCode.PLATFORM_UNAVAILABLE
        mock_get.assert_called_once()

        await plugin.close()

    @pytest.mark.asyncio
    async def test_iam_refresh_business_failure_raises_platform_error(self):
        """HTTP 200 但 success=false → PLATFORM_ERROR（error 是错误码，可入消息）。"""
        mock_post = MagicMock(
            return_value=_mock_caller_response(_caller_ready_body("sbx-123"))
        )
        mock_get = MagicMock(
            return_value=_mock_caller_response(
                {"success": False, "error": "CALLER_OUTBOUND_UPDATE_FAILED"}
            )
        )
        plugin = self._make_plugin(mock_post, mock_get)

        with pytest.raises(PaasError) as exc_info:
            await self._call(plugin)

        assert exc_info.value.code == ErrorCode.PLATFORM_ERROR
        assert "CALLER_OUTBOUND_UPDATE_FAILED" in str(exc_info.value)
        mock_get.assert_called_once()

        await plugin.close()

    @pytest.mark.asyncio
    async def test_connection_missing_when_ready_raises_platform_error(self):
        """need_poll=false without a connection breaks the contract."""
        body = {
            "success": True,
            "message": "获取成功",
            "error_code": 0,
            "data": {
                "instance": {"status": "success"},
                "connection": None,
                "need_poll": False,
            },
        }
        mock_post = MagicMock(return_value=_mock_caller_response(body))
        plugin = self._make_plugin(mock_post)

        with pytest.raises(PaasError) as exc_info:
            await self._call(plugin)

        assert exc_info.value.code == ErrorCode.PLATFORM_ERROR

        await plugin.close()

    @pytest.mark.asyncio
    async def test_shape_violation_fails_fast_without_polling(self, monkeypatch):
        """success=true with a malformed data payload breaks the envelope
        contract → immediate PLATFORM_ERROR, no re-query."""
        monkeypatch.setattr(f"{self._MODULE}._CALLER_POLL_INITIAL_DELAY_SECONDS", 0.0)
        mock_post = MagicMock(
            return_value=_mock_caller_response(
                {"success": True, "message": "ok", "error_code": 0, "data": "oops"}
            )
        )
        plugin = self._make_plugin(mock_post)

        with pytest.raises(PaasError) as exc_info:
            await self._call(plugin)

        assert exc_info.value.code == ErrorCode.PLATFORM_ERROR
        mock_post.assert_called_once()

        await plugin.close()

    @pytest.mark.asyncio
    async def test_transport_error_then_success(self, monkeypatch):
        """A timed-out request may still have been executed server-side —
        transport errors are re-queried within the deadline."""
        monkeypatch.setattr(f"{self._MODULE}._CALLER_POLL_INITIAL_DELAY_SECONDS", 0.0)
        mock_post = MagicMock(
            side_effect=[
                aiohttp.ClientError("connection reset"),
                _mock_caller_response(_caller_ready_body("sbx-retry")),
            ]
        )
        plugin = self._make_plugin(mock_post)

        result = await self._call(plugin)

        assert result == "sbx-retry"
        assert mock_post.call_count == 2

        await plugin.close()

    @pytest.mark.asyncio
    async def test_transport_error_deadline_raises_platform_unavailable(
        self, monkeypatch
    ):
        monkeypatch.setattr(f"{self._MODULE}._CALLER_POLL_INITIAL_DELAY_SECONDS", 0.02)
        monkeypatch.setattr(f"{self._MODULE}._CALLER_POLL_MAX_DELAY_SECONDS", 0.02)
        monkeypatch.setattr(f"{self._MODULE}._CALLER_POLL_DEADLINE_SECONDS", 0.05)
        mock_post = MagicMock(side_effect=aiohttp.ClientError("backend down"))
        plugin = self._make_plugin(mock_post)

        with pytest.raises(PaasError) as exc_info:
            await self._call(plugin)

        assert exc_info.value.code == ErrorCode.PLATFORM_UNAVAILABLE
        assert mock_post.call_count >= 2

        await plugin.close()
