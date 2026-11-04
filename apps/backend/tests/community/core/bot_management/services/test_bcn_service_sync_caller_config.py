"""Tests for BcnService.sync_bot_caller_config.

caller 配置快照同步(PATCH /providers/{pid}/bots/{ref}/caller)。
对应实现: bcn_service.sync_bot_caller_config
风格: LocalHttpClient + MockSeam——stub 响应、断言调用,未 stub 即抛错。
"""
from unittest.mock import Mock, patch

import httpx
import pytest

from agentclaw.community.core.bot_management.services.bcn_service import (
    BcnBotMissingError,
    BcnRequestError,
    BcnService,
    BcnServiceError,
)
from agentclaw.community.di.config import BcnConfig
from agentclaw.community.kernel.bot_manager_sync import DeliveryFailure
from agentclaw.community.plugins.local.http_client import LocalHttpClient

_TEST_BCN_CONFIG = BcnConfig(
    base_url="http://fake-bcn:21000",
    provider_id="prv_40354c8a",
    provider_admin_token="test-bcn-token",
)

_EXPECTED_PATH = "/providers/prv_40354c8a/bots/20260502_1cjjh1ik:100000/caller"
_EXPECTED_PAYLOAD = {
    "snapshot": {
        "source_revision": 5,
        "caller_identity": {
            "enabled": True,
            "minimum_isolation": "caller_container",
        },
    }
}


@pytest.fixture
def http() -> LocalHttpClient:
    return LocalHttpClient(base_url="http://fake-bcn:21000")


@pytest.fixture
def service(http) -> BcnService:
    return BcnService(http_client=http, config=_TEST_BCN_CONFIG, timeout=5.0)


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


def _sync(service: BcnService, *, enabled: bool = True, revision: int = 5) -> dict:
    return service.sync_bot_caller_config(
        teamclaw_bot_uuid="20260502_1cjjh1ik",
        owner_workno="100000",
        enabled=enabled,
        source_revision=revision,
    )


class TestSyncCallerConfigEnvSelection:
    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="dev",
    )
    def test_dev_env_skipped_no_http_call(self, _mock_env, service, http):
        result = _sync(service)

        assert result["skipped"] is True
        assert result["provider_bot_ref"] == "20260502_1cjjh1ik:100000"
        assert http.calls_to("patch") == []


class TestSyncCallerConfigTransport:
    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_applied_returns_sync_status(self, _mock_env, service, http):
        http.set_response("patch", _ok_response(200, {"sync_status": "applied"}))

        result = _sync(service)

        assert result["skipped"] is False
        assert result["sync_status"] == "applied"

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_unchanged_returns_sync_status(self, _mock_env, service, http):
        http.set_response("patch", _ok_response(200, {"sync_status": "unchanged"}))

        result = _sync(service)

        assert result["sync_status"] == "unchanged"

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_envelope_wrapped_success_response_is_unwrapped(self, _mock_env, service, http):
        # corp BCN 管理面会包 {"success": true, "data": {...}} — 必须解开。
        http.set_response("patch", _ok_response(200, {
            "success": True,
            "data": {"sync_status": "applied"},
        }))

        result = _sync(service)

        assert result["sync_status"] == "applied"

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_envelope_failure_raises_with_server_message(self, _mock_env, service, http):
        http.set_response("patch", _ok_response(200, {
            "success": False,
            "error": "bcn down",
        }))

        with pytest.raises(BcnServiceError, match="bcn down"):
            _sync(service)

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_stale_raises_bcn_service_error(self, _mock_env, service, http):
        http.set_response("patch", _ok_response(200, {"sync_status": "stale"}))

        with pytest.raises(BcnServiceError):
            _sync(service)

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_body_missing_sync_status_raises(self, _mock_env, service, http):
        http.set_response("patch", _ok_response(200, {}))

        with pytest.raises(BcnServiceError, match="unexpected response body"):
            _sync(service)

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_http_404_raises_bcn_service_error(self, _mock_env, service, http):
        http.set_response("patch", _ok_response(404, {"error": "provider bot not found"}))

        with pytest.raises(BcnServiceError):
            _sync(service)

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_request_shape_path_payload_and_auth(self, _mock_env, service, http):
        http.set_response("patch", _ok_response(200, {"sync_status": "applied"}))

        _sync(service, enabled=True, revision=5)

        calls = http.calls_to("patch")
        assert len(calls) == 1
        record = calls[0]
        assert record.args[0] == _EXPECTED_PATH
        assert record.kwargs["json"] == _EXPECTED_PAYLOAD
        assert record.kwargs["headers"]["Authorization"] == "Bearer test-bcn-token"

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_timeout_raises_bcn_service_error(self, _mock_env, service, http):
        def _raise_timeout(*_args, **_kwargs):
            raise httpx.TimeoutException("read timeout")

        http.set_override("patch", _raise_timeout)

        with pytest.raises(BcnServiceError):
            _sync(service)


class TestSyncCallerConfigDevRegistrationEnabled:
    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="dev",
    )
    def test_dev_with_registration_enabled_fires_sync(self, _mock_env, http):
        service = BcnService(
            http_client=http,
            config=BcnConfig(
                base_url="http://fake-bcn:21000",
                provider_id="prv_40354c8a",
                provider_admin_token="test-bcn-token",
                provider_registration_enabled=True,
            ),
            timeout=5.0,
        )
        http.set_response("patch", _ok_response(200, {"sync_status": "applied"}))

        result = service.sync_bot_caller_config(
            teamclaw_bot_uuid="20260502_1cjjh1ik",
            owner_workno="100000",
            enabled=True,
            source_revision=5,
        )

        assert result["sync_status"] == "applied"


class TestSyncCallerConfigStableReference:
    """显式 ``provider_bot_ref``(已解析稳定地址)优先;缺省保持既有 owner 拼接。

    Ownership handoff 后当前 owner 不再适用于寻址 —— 显式引用原样进入 URL,
    绝不重拼当前 owner,``source_revision`` 由调用方给定、本调用不改写。
    """

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_explicit_reference_is_used_verbatim_in_path(self, _mock_env, service, http):
        http.set_response("patch", _ok_response(200, {"sync_status": "applied"}))

        result = service.sync_bot_caller_config(
            teamclaw_bot_uuid="b",
            owner_workno="new-owner",
            enabled=True,
            source_revision=5,
            provider_bot_ref="b:stable",
        )

        record = http.calls_to("patch")[0]
        assert record.args[0] == "/providers/prv_40354c8a/bots/b:stable/caller"
        assert "new-owner" not in record.args[0]
        assert record.kwargs["json"]["snapshot"]["source_revision"] == 5
        assert result["skipped"] is False
        assert result["sync_status"] == "applied"
        assert result["provider_bot_ref"] == "b:stable"

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_omitted_reference_keeps_legacy_owner_derived_address(
        self, _mock_env, service, http
    ):
        http.set_response("patch", _ok_response(200, {"sync_status": "unchanged"}))

        result = service.sync_bot_caller_config(
            teamclaw_bot_uuid="b",
            owner_workno="new-owner",
            enabled=True,
            source_revision=5,
        )

        record = http.calls_to("patch")[0]
        assert record.args[0] == "/providers/prv_40354c8a/bots/b:new-owner/caller"
        assert result["provider_bot_ref"] == "b:new-owner"

    @pytest.mark.parametrize("ref", ["", "another:ref"])
    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_invalid_explicit_reference_never_reaches_http(
        self, _mock_env, service, http, ref
    ):
        with pytest.raises(BcnServiceError, match="Invalid provider_bot_ref"):
            service.sync_bot_caller_config(
                teamclaw_bot_uuid="b",
                owner_workno="new-owner",
                enabled=True,
                source_revision=5,
                provider_bot_ref=ref,
            )
        assert http.calls_to("patch") == []


class TestSyncCallerConfigTypedErrors:
    """既有 httpx 捕获点的类型化结果(不解析错误字符串)。"""

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_http_404_raises_bot_missing_error(self, _mock_env, service, http):
        http.set_response("patch", _ok_response(404, {"error": "provider bot not found"}))

        with pytest.raises(BcnBotMissingError):
            _sync(service)

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_timeout_raises_typed_request_error(self, _mock_env, service, http):
        def _raise_timeout(*_args, **_kwargs):
            raise httpx.TimeoutException("read timeout")

        http.set_override("patch", _raise_timeout)

        with pytest.raises(BcnRequestError) as excinfo:
            _sync(service)

        assert excinfo.value.kind == DeliveryFailure.UNKNOWN
        assert excinfo.value.code == "timeout"

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_stale_conflict_raises_typed_caller_conflict_error(
        self, _mock_env, service, http
    ):
        """stale = CAS 冲突:按 REJECTED/caller_conflict 分类,不解析消息字符串。"""
        http.set_response("patch", _ok_response(200, {"sync_status": "stale"}))

        with pytest.raises(BcnRequestError) as excinfo:
            _sync(service)

        assert excinfo.value.kind == DeliveryFailure.REJECTED
        assert excinfo.value.code == "caller_conflict"
        # 既有消息文本保持不变(供人工排障/旧断言继续成立)。
        assert "stale" in str(excinfo.value)
        assert "BCS holds a higher source_revision" in str(excinfo.value)
