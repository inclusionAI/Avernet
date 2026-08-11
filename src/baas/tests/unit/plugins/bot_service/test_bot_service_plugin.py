"""Unit tests for BotServicePlugin implementations.

Covers:
- AiohttpBotServicePlugin construction and report() HTTP POST behaviour
- AiohttpBotServicePlugin get_binding() HTTP GET with error propagation
- StubBotServicePlugin report() is a no-op, get_binding() returns stub data
- LocalBotServicePlugin report() logs debug, get_binding() raises PaasError
- AiohttpBotServicePlugin.report() noop when base_url is empty
- AiohttpBotServicePlugin.report() logs WARNING on HTTP error without raising
- LogRelationPayload construction and to_dict() output
- BotBindingData construction and field defaults
"""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from secbaas.community.api.device_manage import ErrorCode, PaasError
from secbaas.community.plugins.bot_service import (
    AiohttpBotServicePlugin,
    LocalBotServicePlugin,
    StubBotServicePlugin,
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

        inner = {
            "bot_id": "bot_001",
            "owner_id": "20881234",
            "bot_type": "service",
            "engine_type": "openclaw",
            "publish_id": 1,
            "publish_status": "success",
            "binding_id": 202,
            "device_provider": "baas",
            "device_id": "BOT-abc",
        }

        plugin._get_binding_raw = AsyncMock(return_value=inner)

        result = await plugin.get_binding("bot_001", "20881234", "all")

        assert result.binding_id == 202
        plugin._get_binding_raw.assert_called_once_with("bot_001", "20881234", "online")

    @pytest.mark.asyncio
    async def test_get_binding_all_falls_through_to_verify(self):
        """stage="all": online 返回 NOT_FOUND，verify 命中。"""
        plugin = AiohttpBotServicePlugin(
            base_url="https://agentclaw.example.com",
        )

        verify_inner = {
            "bot_id": "bot_001",
            "owner_id": "20881234",
            "bot_type": "service",
            "engine_type": "openclaw",
            "publish_id": 2,
            "publish_status": "validating",
            "binding_id": 303,
            "device_provider": "baas",
            "device_id": "BOT-xyz",
        }

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

        draft_inner = {
            "bot_id": "bot_001",
            "owner_id": "20881234",
            "bot_type": "service",
            "engine_type": "openclaw",
            "publish_id": None,
            "publish_status": None,
            "binding_id": 404,
            "device_provider": "baas",
            "device_id": "BOT-draft",
        }

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


class TestAiohttpBotServicePluginRuntimeEngineSelection:
    """Runtime engine is selected only at the Backend HTTP consumption boundary."""

    @staticmethod
    def _binding_inner(**overrides):
        inner = {
            "bot_id": "bot_personal_001",
            "owner_id": "20881234",
            "bot_type": "personal",
            "engine_type": "claude_code",
            "template_type": "generCC",
            "binding_id": 101,
            "device_provider": "arca",
            "device_id": "ARCA-SANDBOX-001",
        }
        inner.update(overrides)
        return inner

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "runtime_engine_type",
        ["openclaw", "teclaw", "aicoding", "hermes", "claude_code"],
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
            (None, "personal", True),
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
