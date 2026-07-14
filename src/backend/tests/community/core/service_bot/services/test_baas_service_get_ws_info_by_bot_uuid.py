"""Unit tests for BaasService.get_ws_info_by_bot_uuid.

This method is the core implementation for getting WebSocket connection info
directly via bot_uuid, without requiring a bind_id lookup.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BaasServiceError,
    BotWsConnectionInfoResponse,
)


def _make_service_with_response(response_data: dict, status_code: int = 200) -> BaasService:
    """BaasService with a mocked HTTP client that returns the given response data."""
    http_resp = MagicMock()
    http_resp.json.return_value = response_data
    http_resp.raise_for_status = MagicMock()

    http_client = MagicMock()
    http_client.get.return_value = http_resp

    return BaasService(
        baas_api_base="http://baas.test",
        tenant="tnt",
        template_uuid="tpl",
        bot_repo=MagicMock(),
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(),
        storage_path=MagicMock(),
        device_binding_repo=MagicMock(),
        default_ttl_minutes=10080,
        sandbox_registry=MagicMock(),
        http_client=http_client,
        general_http_client=MagicMock(),
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
        outbound_rule_provider=MagicMock(),
    )


def _make_service_raising(status_code: int, body: str) -> BaasService:
    """BaasService whose HTTP client raises an HTTPStatusError."""
    request = httpx.Request("GET", "http://baas.test/api/v1/bots/BOT-xyz/ws-info")
    response = httpx.Response(status_code, request=request, text=body)
    err = httpx.HTTPStatusError(f"{status_code}", request=request, response=response)
    http_resp = MagicMock()
    http_resp.raise_for_status.side_effect = err
    http_client = MagicMock()
    http_client.get.return_value = http_resp

    return BaasService(
        baas_api_base="http://baas.test",
        tenant="tnt",
        template_uuid="tpl",
        bot_repo=MagicMock(),
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(),
        storage_path=MagicMock(),
        device_binding_repo=MagicMock(),
        default_ttl_minutes=10080,
        sandbox_registry=MagicMock(),
        http_client=http_client,
        general_http_client=MagicMock(),
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
        outbound_rule_provider=MagicMock(),
    )


def _ws_info_by_bot_uuid_logged_calls(spy: MagicMock, level: str) -> list:
    """get_ws_info_by_bot_uuid log calls at the given level (warning/error)."""
    return [
        c for c in getattr(spy, level).call_args_list
        if c.args and "get_ws_info_by_bot_uuid" in str(c.args[0])
    ]


@pytest.mark.unit
class TestGetWsInfoByBotUuid:
    """Tests for BaasService.get_ws_info_by_bot_uuid."""

    def test_success_returns_ws_info(self):
        """Successful API call returns BotWsConnectionInfoResponse."""
        response_data = {
            "code": 0,
            "data": {
                "ws_url": "wss://baas.test/ws/BOT-xyz",
                "token": "tok123",
                "target": "TARGET-123",
                "expires_at": "2026-01-01T00:00:00Z",
            },
        }
        service = _make_service_with_response(response_data)

        result = service.get_ws_info_by_bot_uuid(bot_uuid="BOT-xyz")

        assert isinstance(result, BotWsConnectionInfoResponse)
        assert result.ws_url == "wss://baas.test/ws/BOT-xyz"
        assert result.token == "tok123"
        assert result.target == "TARGET-123"
        assert result.expires_at == "2026-01-01T00:00:00Z"
        assert result.paas_device_id == "BOT-xyz"
        assert result.bot_uuid == "BOT-xyz"
        assert result.tenant == "tnt"
        assert result.baas_base_url == "http://baas.test"
        assert result.engine_port == 20003  # default

    def test_custom_port_and_path(self):
        """Custom port and path are passed to API."""
        response_data = {
            "code": 0,
            "data": {
                "ws_url": "wss://baas.test/ws/BOT-xyz",
                "token": "tok123",
                "target": "TARGET-123",
                "expires_at": "2026-01-01T00:00:00Z",
            },
        }
        http_resp = MagicMock()
        http_resp.json.return_value = response_data
        http_resp.raise_for_status = MagicMock()

        http_client = MagicMock()
        http_client.get.return_value = http_resp

        service = BaasService(
            baas_api_base="http://baas.test",
            tenant="tnt",
            template_uuid="tpl",
            bot_repo=MagicMock(),
            bot_publish_repo=MagicMock(),
            system_config_service=MagicMock(),
            storage_path=MagicMock(),
            device_binding_repo=MagicMock(),
            default_ttl_minutes=10080,
            sandbox_registry=MagicMock(),
            http_client=http_client,
            general_http_client=MagicMock(),
            secret_resolver=MagicMock(),
            common_whitelist_service=MagicMock(),
            outbound_rule_provider=MagicMock(),
        )

        service.get_ws_info_by_bot_uuid(
            bot_uuid="BOT-xyz",
            port=18900,
            path="custom/ws/path",
        )

        call_args = http_client.get.call_args
        assert call_args[0][0] == "/api/v1/bots/BOT-xyz/ws-info"
        assert call_args[1]["params"]["port"] == 18900
        assert call_args[1]["params"]["path"] == "custom/ws/path"

    def test_device_affinity_and_device_uuid_passed(self):
        """device_affinity and device_uuid are passed to API params."""
        response_data = {
            "code": 0,
            "data": {
                "ws_url": "wss://baas.test/ws/BOT-xyz",
                "token": "tok123",
                "target": "TARGET-123",
                "expires_at": "2026-01-01T00:00:00Z",
            },
        }
        http_resp = MagicMock()
        http_resp.json.return_value = response_data
        http_resp.raise_for_status = MagicMock()

        http_client = MagicMock()
        http_client.get.return_value = http_resp

        service = BaasService(
            baas_api_base="http://baas.test",
            tenant="tnt",
            template_uuid="tpl",
            bot_repo=MagicMock(),
            bot_publish_repo=MagicMock(),
            system_config_service=MagicMock(),
            storage_path=MagicMock(),
            device_binding_repo=MagicMock(),
            default_ttl_minutes=10080,
            sandbox_registry=MagicMock(),
            http_client=http_client,
            general_http_client=MagicMock(),
            secret_resolver=MagicMock(),
            common_whitelist_service=MagicMock(),
            outbound_rule_provider=MagicMock(),
        )

        service.get_ws_info_by_bot_uuid(
            bot_uuid="BOT-xyz",
            device_affinity="user-123",
            device_uuid="device-instance-456",
        )

        call_args = http_client.get.call_args
        assert call_args[1]["params"]["device_affinity"] == "user-123"
        assert call_args[1]["params"]["device_uuid"] == "device-instance-456"

    def test_custom_tenant(self):
        """Custom tenant overrides default tenant."""
        response_data = {
            "code": 0,
            "data": {
                "ws_url": "wss://baas.test/ws/BOT-xyz",
                "token": "tok123",
                "target": "TARGET-123",
                "expires_at": "2026-01-01T00:00:00Z",
            },
        }
        http_resp = MagicMock()
        http_resp.json.return_value = response_data
        http_resp.raise_for_status = MagicMock()

        http_client = MagicMock()
        http_client.get.return_value = http_resp

        service = BaasService(
            baas_api_base="http://baas.test",
            tenant="default-tenant",
            template_uuid="tpl",
            bot_repo=MagicMock(),
            bot_publish_repo=MagicMock(),
            system_config_service=MagicMock(),
            storage_path=MagicMock(),
            device_binding_repo=MagicMock(),
            default_ttl_minutes=10080,
            sandbox_registry=MagicMock(),
            http_client=http_client,
            general_http_client=MagicMock(),
            secret_resolver=MagicMock(),
            common_whitelist_service=MagicMock(),
            outbound_rule_provider=MagicMock(),
        )

        result = service.get_ws_info_by_bot_uuid(
            bot_uuid="BOT-xyz",
            tenant="custom-tenant",
        )

        call_args = http_client.get.call_args
        assert call_args[1]["params"]["tenant"] == "custom-tenant"
        assert result.tenant == "custom-tenant"

    def test_api_error_code_raises_baas_error(self):
        """Non-zero code in response raises BaasServiceError."""
        response_data = {
            "code": 1,
            "message": "Bot not found",
        }
        http_resp = MagicMock()
        http_resp.json.return_value = response_data
        http_resp.raise_for_status = MagicMock()

        http_client = MagicMock()
        http_client.get.return_value = http_resp

        service = BaasService(
            baas_api_base="http://baas.test",
            tenant="tnt",
            template_uuid="tpl",
            bot_repo=MagicMock(),
            bot_publish_repo=MagicMock(),
            system_config_service=MagicMock(),
            storage_path=MagicMock(),
            device_binding_repo=MagicMock(),
            default_ttl_minutes=10080,
            sandbox_registry=MagicMock(),
            http_client=http_client,
            general_http_client=MagicMock(),
            secret_resolver=MagicMock(),
            common_whitelist_service=MagicMock(),
            outbound_rule_provider=MagicMock(),
        )

        with pytest.raises(BaasServiceError, match="Bot not found"):
            service.get_ws_info_by_bot_uuid(bot_uuid="BOT-xyz")

    def test_404_logs_warning_not_error(self):
        """404 HTTP error logs at WARNING, not ERROR."""
        body = (
            '{"detail":{"error":"BOT_NOT_FOUND",'
            '"message":"Bot not found","bot_uuid":"BOT-xyz"}}'
        )
        service = _make_service_raising(404, body)
        with patch(
            "agentclaw.community.core.service_bot.services.baas_service.logger"
        ) as spy:
            with pytest.raises(BaasServiceError):
                service.get_ws_info_by_bot_uuid(bot_uuid="BOT-xyz")

        assert _ws_info_by_bot_uuid_logged_calls(spy, "warning"), "404 should log at WARNING"
        assert not _ws_info_by_bot_uuid_logged_calls(spy, "error"), "404 must NOT log at ERROR"

    def test_503_logs_warning_not_error(self):
        """503 HTTP error logs at WARNING, not ERROR."""
        body = (
            '{"detail":{"error":"NO_ACTIVE_DEVICES",'
            '"message":"No active devices available"}}'
        )
        service = _make_service_raising(503, body)
        with patch(
            "agentclaw.community.core.service_bot.services.baas_service.logger"
        ) as spy:
            with pytest.raises(BaasServiceError):
                service.get_ws_info_by_bot_uuid(bot_uuid="BOT-xyz")

        assert _ws_info_by_bot_uuid_logged_calls(spy, "warning"), "503 should log at WARNING"
        assert not _ws_info_by_bot_uuid_logged_calls(spy, "error"), "503 must NOT log at ERROR"

    def test_http_error_still_raises_baas_service_error(self):
        """HTTP errors are still surfaced to callers as BaasServiceError."""
        service = _make_service_raising(500, "Internal Server Error")
        with pytest.raises(BaasServiceError):
            service.get_ws_info_by_bot_uuid(bot_uuid="BOT-xyz")

    def test_generic_exception_raises_baas_service_error(self):
        """Generic exceptions are wrapped in BaasServiceError."""
        http_client = MagicMock()
        http_client.get.side_effect = Exception("Network error")

        service = BaasService(
            baas_api_base="http://baas.test",
            tenant="tnt",
            template_uuid="tpl",
            bot_repo=MagicMock(),
            bot_publish_repo=MagicMock(),
            system_config_service=MagicMock(),
            storage_path=MagicMock(),
            device_binding_repo=MagicMock(),
            default_ttl_minutes=10080,
            sandbox_registry=MagicMock(),
            http_client=http_client,
            general_http_client=MagicMock(),
            secret_resolver=MagicMock(),
            common_whitelist_service=MagicMock(),
            outbound_rule_provider=MagicMock(),
        )

        with patch(
            "agentclaw.community.core.service_bot.services.baas_service.logger"
        ):
            with pytest.raises(BaasServiceError, match="Failed to get ws info"):
                service.get_ws_info_by_bot_uuid(bot_uuid="BOT-xyz")


@pytest.mark.unit
class TestGetWsInfoDelegation:
    """Tests that get_ws_info delegates to get_ws_info_by_bot_uuid."""

    def test_get_ws_info_uses_binding_device_id(self):
        """get_ws_info looks up binding and passes device_id to get_ws_info_by_bot_uuid."""
        response_data = {
            "code": 0,
            "data": {
                "ws_url": "wss://baas.test/ws/DEV-123",
                "token": "tok123",
                "target": "TARGET-123",
                "expires_at": "2026-01-01T00:00:00Z",
            },
        }
        http_resp = MagicMock()
        http_resp.json.return_value = response_data
        http_resp.raise_for_status = MagicMock()

        http_client = MagicMock()
        http_client.get.return_value = http_resp

        binding = MagicMock()
        binding.device_id = "DEV-123"
        binding_repo = MagicMock()
        binding_repo.get_by_id.return_value = binding

        service = BaasService(
            baas_api_base="http://baas.test",
            tenant="tnt",
            template_uuid="tpl",
            bot_repo=MagicMock(),
            bot_publish_repo=MagicMock(),
            system_config_service=MagicMock(),
            storage_path=MagicMock(),
            device_binding_repo=binding_repo,
            default_ttl_minutes=10080,
            sandbox_registry=MagicMock(),
            http_client=http_client,
            general_http_client=MagicMock(),
            secret_resolver=MagicMock(),
            common_whitelist_service=MagicMock(),
            outbound_rule_provider=MagicMock(),
        )

        result = service.get_ws_info(bind_id=42)

        # Verify binding was looked up
        binding_repo.get_by_id.assert_called_once_with(42)

        # Verify HTTP call uses device_id from binding
        call_args = http_client.get.call_args
        assert call_args[0][0] == "/api/v1/bots/DEV-123/ws-info"

        # Verify result
        assert result.bot_uuid == "DEV-123"
        assert result.paas_device_id == "DEV-123"

    def test_get_ws_info_raises_when_binding_not_found(self):
        """get_ws_info raises BaasServiceError when binding doesn't exist."""
        binding_repo = MagicMock()
        binding_repo.get_by_id.return_value = None

        service = BaasService(
            baas_api_base="http://baas.test",
            tenant="tnt",
            template_uuid="tpl",
            bot_repo=MagicMock(),
            bot_publish_repo=MagicMock(),
            system_config_service=MagicMock(),
            storage_path=MagicMock(),
            device_binding_repo=binding_repo,
            default_ttl_minutes=10080,
            sandbox_registry=MagicMock(),
            http_client=MagicMock(),
            general_http_client=MagicMock(),
            secret_resolver=MagicMock(),
            common_whitelist_service=MagicMock(),
            outbound_rule_provider=MagicMock(),
        )

        with pytest.raises(BaasServiceError, match="Device binding not found"):
            service.get_ws_info(bind_id=999)

    def test_get_ws_info_forwards_parameters(self):
        """get_ws_info forwards all optional parameters to get_ws_info_by_bot_uuid."""
        response_data = {
            "code": 0,
            "data": {
                "ws_url": "wss://baas.test/ws/DEV-123",
                "token": "tok123",
                "target": "TARGET-123",
                "expires_at": "2026-01-01T00:00:00Z",
            },
        }
        http_resp = MagicMock()
        http_resp.json.return_value = response_data
        http_resp.raise_for_status = MagicMock()

        http_client = MagicMock()
        http_client.get.return_value = http_resp

        binding = MagicMock()
        binding.device_id = "DEV-123"
        binding_repo = MagicMock()
        binding_repo.get_by_id.return_value = binding

        service = BaasService(
            baas_api_base="http://baas.test",
            tenant="default-tenant",
            template_uuid="tpl",
            bot_repo=MagicMock(),
            bot_publish_repo=MagicMock(),
            system_config_service=MagicMock(),
            storage_path=MagicMock(),
            device_binding_repo=binding_repo,
            default_ttl_minutes=10080,
            sandbox_registry=MagicMock(),
            http_client=http_client,
            general_http_client=MagicMock(),
            secret_resolver=MagicMock(),
            common_whitelist_service=MagicMock(),
            outbound_rule_provider=MagicMock(),
        )

        result = service.get_ws_info(
            bind_id=42,
            port=18900,
            path="custom/path",
            tenant="override-tenant",
            device_affinity="user-123",
            device_uuid="device-instance-456",
        )

        # Verify all parameters were forwarded
        call_args = http_client.get.call_args
        params = call_args[1]["params"]
        assert params["port"] == 18900
        assert params["path"] == "custom/path"
        assert params["tenant"] == "override-tenant"
        assert params["device_affinity"] == "user-123"
        assert params["device_uuid"] == "device-instance-456"

        assert result.tenant == "override-tenant"
        assert result.engine_port == 18900