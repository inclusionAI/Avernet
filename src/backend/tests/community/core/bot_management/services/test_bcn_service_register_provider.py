"""Tests for BcnService.register_provider_bot.

claude_code engine 启动时把 bot 注册到 BCN Provider (下行链路) 的接口测试.
对应实现: agentclaw.community.core.bot_management.services.bcn_service.register_provider_bot

BcnService talks to BCN through an injected :class:`HttpClient`; tests drive it
with a :class:`LocalHttpClient` (stub the response, assert the call).
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


class TestRegisterProviderBotEnvSelection:
    """env=dev 时跳过, env=pre/prod 走真实 HTTP 路径."""

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="dev",
    )
    def test_dev_env_skipped_no_http_call(self, _mock_env, service, http):
        result = service.register_provider_bot(
            teamclaw_bot_uuid="20260502_1cjjh1ik",
            owner_workno="100000",
            name="Bot",
            summary="",
        )

        assert result["skipped"] is True
        assert result["provider_bot_ref"] == "20260502_1cjjh1ik:100000"
        assert result["bot_runtime_token"] == ""
        assert http.calls_to("post") == []

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_pre_env_uses_pre_provider_credentials(self, _mock_env, service, http):
        http.set_response("post", _ok_response(200, {
            "bot_uuid": "u1",
            "provider_id": "prv_40354c8a",
            "provider_bot_ref": "20260502_1cjjh1ik:100000",
            "bot_runtime_token": "tok-pre",
        }))

        service.register_provider_bot(
            teamclaw_bot_uuid="20260502_1cjjh1ik",
            owner_workno="100000",
            name="Bot",
            summary="some summary",
        )

        call = http.calls_to("post")[0]
        assert call.args[0] == "/providers/prv_40354c8a/bots"
        assert call.kwargs["headers"]["Authorization"] == (
            "Bearer test-bcn-token-pre"
        )
        assert call.kwargs["headers"]["Content-Type"] == "application/json"
        payload = call.kwargs["json"]
        assert payload["name"] == "Bot"
        assert payload["summary"] == "some summary"
        assert payload["owners"] == ["100000"]
        assert payload["provider_bot_ref"] == "20260502_1cjjh1ik:100000"

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_prod_env_uses_prod_provider_credentials(self, _mock_env, service, http):
        http.set_response("post", _ok_response(
            200, {"bot_uuid": "u2", "bot_runtime_token": "tok-prod"}
        ))

        service.register_provider_bot(
            teamclaw_bot_uuid="bot-uuid",
            owner_workno="123",
            name="Bot",
            summary="",
        )

        call = http.calls_to("post")[0]
        assert call.args[0] == "/providers/prv_4b7fce5b/bots"
        assert call.kwargs["headers"]["Authorization"] == (
            "Bearer test-bcn-token-prod"
        )

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_pre_env_missing_token_skipped_no_http_call(self, _mock_env, http):
        config = BcnConfig(
            base_url="http://fake-bcn:21000",
            provider_id_prod="prv_4b7fce5b",
            provider_id_pre="prv_40354c8a",
            provider_admin_token_prod="test-bcn-token-prod",
            provider_admin_token_pre="",
        )
        service = BcnService(
            http_client=http,
            config=config,
            timeout=5.0,
        )

        result = service.register_provider_bot(
            teamclaw_bot_uuid="bot-uuid",
            owner_workno="123",
            name="Bot",
            summary="",
        )

        assert result["skipped"] is True
        assert result["provider_bot_ref"] == "bot-uuid:123"
        assert http.calls_to("post") == []


class TestRegisterProviderBotErrorHandling:

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_409_treated_as_idempotent_success(self, _mock_env, service, http):
        # 409 = (provider_id, provider_bot_ref) 已绑过, 视作幂等成功不抛
        http.set_response("post", Mock(
            status_code=409,
            json=Mock(return_value={"bot_uuid": "existed"}),
            text="",
        ))

        result = service.register_provider_bot(
            teamclaw_bot_uuid="bot-uuid",
            owner_workno="123",
            name="Bot",
            summary="",
        )

        assert result["idempotent_replay"] is True
        assert result["bot_uuid"] == "existed"
        assert result["provider_bot_ref"] == "bot-uuid:123"

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_4xx_raises_BcnServiceError(self, _mock_env, service, http):
        http.set_response("post", _ok_response(400))

        with pytest.raises(BcnServiceError):
            service.register_provider_bot(
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
            service.register_provider_bot(
                teamclaw_bot_uuid="bot-uuid",
                owner_workno="123",
                name="Bot",
                summary="",
            )

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_409_with_unparseable_body_still_idempotent(self, _mock_env, service, http):
        # 409 但 response.json() 抛 ValueError → 走 except 分支用空 dict 回填
        bad_response = Mock(status_code=409, text="")
        bad_response.json.side_effect = ValueError("not json")
        http.set_response("post", bad_response)

        result = service.register_provider_bot(
            teamclaw_bot_uuid="bot-uuid",
            owner_workno="123",
            name="Bot",
            summary="",
        )

        assert result["idempotent_replay"] is True
        assert result["provider_bot_ref"] == "bot-uuid:123"
        assert result["provider_id"] == "prv_40354c8a"

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_unexpected_exception_raises_BcnServiceError(self, _mock_env, service, http):
        # 非 HTTPStatusError / TimeoutException 的异常走通用兜底分支
        http.set_override("post", _raise(RuntimeError("network adapter exploded")))

        with pytest.raises(BcnServiceError) as excinfo:
            service.register_provider_bot(
                teamclaw_bot_uuid="bot-uuid",
                owner_workno="123",
                name="Bot",
                summary="",
            )

        assert "network adapter exploded" in str(excinfo.value)


class TestDeleteProviderBot:
    """BCN Provider Bot 逻辑删除接口测试."""

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="dev",
    )
    def test_dev_env_skipped_no_http_call(self, _mock_env, service, http):
        result = service.delete_provider_bot(
            teamclaw_bot_uuid="20260611_d5v7rui3",
            owner_workno="100000",
        )

        assert result["skipped"] is True
        assert result["provider_bot_ref"] == "20260611_d5v7rui3:100000"
        assert http.calls_to("delete") == []

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_pre_env_uses_pre_provider_credentials(self, _mock_env, service, http):
        http.set_response("delete", _ok_response(204, {}))

        result = service.delete_provider_bot(
            teamclaw_bot_uuid="20260611_d5v7rui3",
            owner_workno="100000",
        )

        assert result["deleted"] is True
        assert result["provider_id"] == "prv_40354c8a"
        assert result["provider_bot_ref"] == "20260611_d5v7rui3:100000"
        call = http.calls_to("delete")[0]
        assert call.args[0] == "/providers/prv_40354c8a/bots/20260611_d5v7rui3:100000"
        assert call.kwargs["headers"]["Authorization"] == (
            "Bearer test-bcn-token-pre"
        )

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_4xx_raises_BcnServiceError(self, _mock_env, service, http):
        http.set_response("delete", _ok_response(400))

        with pytest.raises(BcnServiceError):
            service.delete_provider_bot(
                teamclaw_bot_uuid="bot-uuid",
                owner_workno="123",
            )

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_timeout_raises_BcnServiceError(self, _mock_env, service, http):
        http.set_override("delete", _raise(httpx.TimeoutException("slow")))

        with pytest.raises(BcnServiceError):
            service.delete_provider_bot(
                teamclaw_bot_uuid="bot-uuid",
                owner_workno="123",
            )
