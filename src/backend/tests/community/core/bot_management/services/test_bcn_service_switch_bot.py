"""Tests for BcnService.switch_bot.

Bot 切换绑定接口测试.
对应实现: agentclaw.community.core.bot_management.services.bcn_service.switch_bot

BcnService now talks to BCN through an injected :class:`HttpClient`; tests drive
it with a :class:`LocalHttpClient` (stub the response, assert the call) instead
of patching ``httpx.Client``.
"""
from typing import Callable
from unittest.mock import Mock, patch

import httpx
import pytest

from agentclaw.community.core.bot_management.services.bcn_service import (
    BcnService,
    BcnServiceError,
)
from agentclaw.community.di.config import BcnConfig
from agentclaw.community.plugins.local.http_client import LocalHttpClient


# Down-link provider config the tests assert on. In production this comes from
# the ``bcn`` yaml block (corp env overlays); here it is injected directly.
# provider_id_* are non-sensitive identifiers; the admin tokens are fake.
_TEST_BCN_CONFIG = BcnConfig(
    base_url="http://fake-bcn:21000",
    provider_id_prod="prv_4b7fce5b",
    provider_id_pre="prv_40354c8a",
    provider_admin_token_prod="test-bcn-token-prod",
    provider_admin_token_pre="test-bcn-token-pre",
)


@pytest.fixture
def http() -> LocalHttpClient:
    return LocalHttpClient(base_url="http://fake-bcn:21000")


@pytest.fixture
def service(http) -> BcnService:
    return BcnService(
        http_client=http,
        config=_TEST_BCN_CONFIG,
        timeout=5.0,
    )


def _ok_response(status: int = 200, body: dict | None = None) -> Mock:
    resp = Mock()
    resp.status_code = status
    resp.json.return_value = body or {}
    resp.text = ""
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "boom", request=Mock(), response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _raise(exc: Exception) -> Callable[..., object]:
    def _fn(*_args, **_kwargs):
        raise exc
    return _fn


class TestSwitchBotEnvSelection:
    """env=dev 时跳过, env=pre/prod 走真实 HTTP 路径."""

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="dev",
    )
    def test_dev_env_skipped_no_http_call(self, _mock_env, service, http):
        result = service.switch_bot(
            teamclaw_bot_uuid="20260502_1cjjh1ik",
            owner_workno="100000",
            name="Bot",
            summary="Test summary",
        )

        assert result["skipped"] is True
        assert result["provider_bot_ref"] == "20260502_1cjjh1ik:100000"
        assert result["token"] == ""
        assert result["bot_id"] == ""
        assert http.calls_to("post") == []

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_pre_env_uses_pre_provider_credentials(self, _mock_env, service, http):
        http.set_response("post", _ok_response(200, {
            "success": True,
            "data": {
                "bot_id": "bot-123",
                "provider_id": "prv_40354c8a",
                "provider_bot_ref": "20260502_1cjjh1ik:100000",
                "token": "tok-pre",
                "binding_created_at": 1748764800000,
                "idempotent_replay": False,
                "websocket_kicked": True,
            },
        }))

        result = service.switch_bot(
            teamclaw_bot_uuid="20260502_1cjjh1ik",
            owner_workno="100000",
            name="Bot",
            summary="some summary",
        )

        # Verify result is the data dict
        assert result["bot_id"] == "bot-123"
        assert result["provider_id"] == "prv_40354c8a"
        assert result["provider_bot_ref"] == "20260502_1cjjh1ik:100000"
        assert result["token"] == "tok-pre"
        assert result["websocket_kicked"] is True

        # Verify request (path is relative; base_url lives on the client)
        call = http.calls_to("post")[0]
        assert call.args[0] == "/providers/prv_40354c8a/delivery/switch-bot"
        assert call.kwargs["headers"]["Authorization"] == (
            "Bearer test-bcn-token-pre"
        )
        assert call.kwargs["headers"]["Content-Type"] == "application/json"
        payload = call.kwargs["json"]
        assert payload["name"] == "Bot"
        assert payload["summary"] == "some summary"
        assert payload["bot_id"] == "20260502_1cjjh1ik:100000"
        assert payload["provider_bot_ref"] == "20260502_1cjjh1ik:100000"

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_prod_env_uses_prod_provider_credentials(self, _mock_env, service, http):
        http.set_response("post", _ok_response(200, {
            "success": True,
            "data": {
                "bot_id": "bot-prod",
                "provider_id": "prv_4b7fce5b",
                "token": "tok-prod",
                "websocket_kicked": False,
            },
        }))

        service.switch_bot(
            teamclaw_bot_uuid="bot-uuid",
            owner_workno="123",
            name="Bot",
            summary="",
        )

        call = http.calls_to("post")[0]
        assert call.args[0] == "/providers/prv_4b7fce5b/delivery/switch-bot"
        assert call.kwargs["headers"]["Authorization"] == (
            "Bearer test-bcn-token-prod"
        )


class TestSwitchBotSuccessResponse:
    """成功响应处理."""

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_success_response_returns_data_dict(self, _mock_env, service, http):
        http.set_response("post", _ok_response(200, {
            "success": True,
            "data": {
                "bot_id": "bot-456",
                "provider_id": "prv_40354c8a",
                "provider_bot_ref": "bot-uuid:123",
                "token": "new-token",
                "binding_created_at": 1748764800000,
                "idempotent_replay": False,
                "websocket_kicked": True,
            },
        }))

        result = service.switch_bot(
            teamclaw_bot_uuid="bot-uuid",
            owner_workno="123",
            name="My Bot",
            summary="A helpful bot",
        )

        assert result["bot_id"] == "bot-456"
        assert result["token"] == "new-token"
        assert result["websocket_kicked"] is True
        assert result["idempotent_replay"] is False
        assert "skipped" not in result

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_idempotent_replay_true(self, _mock_env, service, http):
        http.set_response("post", _ok_response(200, {
            "success": True,
            "data": {
                "bot_id": "bot-789",
                "provider_id": "prv_40354c8a",
                "provider_bot_ref": "bot-uuid:123",
                "token": "existing-token",
                "binding_created_at": 1748764800000,
                "idempotent_replay": True,
                "websocket_kicked": False,
            },
        }))

        result = service.switch_bot(
            teamclaw_bot_uuid="bot-uuid",
            owner_workno="123",
            name="Bot",
            summary="",
        )

        assert result["idempotent_replay"] is True
        assert result["websocket_kicked"] is False


class TestSwitchBotErrorHandling:
    """错误处理."""

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_success_false_raises_BcnServiceError(self, _mock_env, service, http):
        http.set_response("post", _ok_response(200, {
            "success": False,
            "error": "Provider not found",
        }))

        with pytest.raises(BcnServiceError) as excinfo:
            service.switch_bot(
                teamclaw_bot_uuid="bot-uuid",
                owner_workno="123",
                name="Bot",
                summary="",
            )

        assert "Provider not found" in str(excinfo.value)

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_success_false_with_message_raises_BcnServiceError(self, _mock_env, service, http):
        http.set_response("post", _ok_response(200, {
            "success": False,
            "message": "Invalid bot_id format",
        }))

        with pytest.raises(BcnServiceError) as excinfo:
            service.switch_bot(
                teamclaw_bot_uuid="bot-uuid",
                owner_workno="123",
                name="Bot",
                summary="",
            )

        assert "Invalid bot_id format" in str(excinfo.value)

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_success_false_no_error_field_uses_unknown(self, _mock_env, service, http):
        http.set_response("post", _ok_response(200, {
            "success": False,
        }))

        with pytest.raises(BcnServiceError) as excinfo:
            service.switch_bot(
                teamclaw_bot_uuid="bot-uuid",
                owner_workno="123",
                name="Bot",
                summary="",
            )

        assert "Unknown error" in str(excinfo.value)

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_4xx_raises_BcnServiceError(self, _mock_env, service, http):
        http.set_response("post", _ok_response(400))

        with pytest.raises(BcnServiceError):
            service.switch_bot(
                teamclaw_bot_uuid="bot-uuid",
                owner_workno="123",
                name="Bot",
                summary="",
            )

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_5xx_raises_BcnServiceError(self, _mock_env, service, http):
        http.set_response("post", _ok_response(500))

        with pytest.raises(BcnServiceError):
            service.switch_bot(
                teamclaw_bot_uuid="bot-uuid",
                owner_workno="123",
                name="Bot",
                summary="",
            )

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_timeout_raises_BcnServiceError(self, _mock_env, service, http):
        http.set_override("post", _raise(httpx.TimeoutException("slow")))

        with pytest.raises(BcnServiceError):
            service.switch_bot(
                teamclaw_bot_uuid="bot-uuid",
                owner_workno="123",
                name="Bot",
                summary="",
            )

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_unexpected_exception_raises_BcnServiceError(self, _mock_env, service, http):
        http.set_override("post", _raise(RuntimeError("network adapter exploded")))

        with pytest.raises(BcnServiceError) as excinfo:
            service.switch_bot(
                teamclaw_bot_uuid="bot-uuid",
                owner_workno="123",
                name="Bot",
                summary="",
            )

        assert "network adapter exploded" in str(excinfo.value)


class TestSwitchBotBotIdFormat:
    """provider_bot_ref 格式验证: {teamclaw_bot_uuid}:{owner_workno}"""

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_bot_id_and_provider_bot_ref_format(self, _mock_env, service, http):
        http.set_response("post", _ok_response(200, {
            "success": True,
            "data": {
                "bot_id": "bot-id",
                "token": "token",
            },
        }))

        service.switch_bot(
            teamclaw_bot_uuid="20260502_abc123",
            owner_workno="85020",
            name="Test Bot",
            summary="Test",
        )

        call = http.calls_to("post")[0]
        payload = call.kwargs["json"]
        # Both bot_id and provider_bot_ref should be "{uuid}:{workno}"
        assert payload["bot_id"] == "20260502_abc123:85020"
        assert payload["provider_bot_ref"] == "20260502_abc123:85020"
