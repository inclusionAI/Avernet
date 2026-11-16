"""Task 2 BCN transport extensions: typed request errors + single-send timeout.

既有 httpx 捕获点的类型化结果 —— 不再靠解析错误字符串识别 404/401/403/503/超时:

- 404 → :class:`BcnBotMissingError` (bot 不存在);
- 503 → :class:`BcnUnavailableError` (BCN 不可用);
- 401/403 等 4xx → :class:`BcnRequestError` (kind=REJECTED, code=http_<status>);
- 其余 5xx → :class:`BcnRequestError` (kind=UNKNOWN, code=http_<status>);
- 超时 → :class:`BcnRequestError` (kind=UNKNOWN, code=timeout);
- 其他意外异常 → :class:`BcnRequestError` (kind=UNKNOWN, code=unknown)。

全部继承 :class:`BcnServiceError`,旧 ``except BcnServiceError`` 继续兼容;
异常消息只携带固定 code 与 HTTP 状态,绝不携带原始响应 body。

另覆盖本任务新增的单次发送超时 ``timeout_seconds``:显式传入时该次请求
使用给定剩余预算,省略(None)沿用服务的 ``self._timeout`` 既有默认。

对应实现: ``bcn_service`` 既有 httpx 异常捕获处;错误定义在
``bcn_transport_types`` (由 ``bcn_service`` re-export)。
"""

from unittest.mock import Mock, patch

import httpx
import pytest

from agentclaw.community.core.bot_management.services.bcn_service import (
    BcnBotMissingError,
    BcnRequestError,
    BcnService,
    BcnServiceError,
    BcnUnavailableError,
)
from agentclaw.community.di.config import BcnConfig
from agentclaw.community.kernel.bot_manager_sync import DeliveryFailure
from agentclaw.community.plugin_api.http_client import HttpClient
from agentclaw.community.plugins.local.http_client import LocalHttpClient

_ENV_PATCH = (
    "agentclaw.community.core.bot_management.services.bcn_service.get_current_env"
)
_TEST_BCN_CONFIG = BcnConfig(
    base_url="http://fake-bcn:21000",
    provider_id="prv_40354c8a",
    provider_admin_token="test-bcn-token",
)


def _post_response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(
        status,
        json=body,
        request=httpx.Request("POST", "https://example.com/admin/bots/onboard"),
    )


def _http_with_post_status(status: int) -> Mock:
    http = Mock(spec=HttpClient)
    http.post.return_value = _post_response(status, {"error": f"secret-{status}-body"})
    return http


def _stub_response(status: int, body: dict | None = None) -> Mock:
    """LocalHttpClient stub response, the register/attributes-suite style."""
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


class TestTypedErrorContract:
    """构造器契约:kind/code 只读;旧 except BcnServiceError 兼容。"""

    def test_request_error_kind_and_code_are_read_only(self):
        err = BcnRequestError(DeliveryFailure.REJECTED, "http_403")

        assert err.kind == DeliveryFailure.REJECTED
        assert err.code == "http_403"
        assert str(err) == "http_403"

        with pytest.raises(AttributeError):
            err.kind = DeliveryFailure.UNKNOWN
        with pytest.raises(AttributeError):
            err.code = "spoofed"

    def test_typed_errors_subclass_bcn_service_error_for_old_catchers(self):
        for exc_type in (BcnBotMissingError, BcnUnavailableError, BcnRequestError):
            assert issubclass(exc_type, BcnServiceError)

        for exc in (
            BcnBotMissingError("missing"),
            BcnUnavailableError("unavailable"),
            BcnRequestError(DeliveryFailure.UNKNOWN, "timeout"),
        ):
            with pytest.raises(BcnServiceError):
                raise exc


class TestOnboardTypedErrors:
    """onboard_bot 的 httpx 捕获点 → 类型化异常 (无 env 门控,Mock(spec=HttpClient))。"""

    @pytest.mark.parametrize("status", [400, 401, 403, 429])
    def test_onboard_4xx_raises_rejected_request_error(self, status):
        service = BcnService(_http_with_post_status(status))

        with pytest.raises(BcnRequestError) as excinfo:
            service.onboard_bot("b:e", "N", "D")

        assert excinfo.value.kind == DeliveryFailure.REJECTED
        assert excinfo.value.code == f"http_{status}"
        assert f"secret-{status}-body" not in str(excinfo.value)

    @pytest.mark.parametrize("status", [500, 502, 504])
    def test_onboard_5xx_raises_unknown_request_error(self, status):
        service = BcnService(_http_with_post_status(status))

        with pytest.raises(BcnRequestError) as excinfo:
            service.onboard_bot("b:e", "N", "D")

        assert excinfo.value.kind == DeliveryFailure.UNKNOWN
        assert excinfo.value.code == f"http_{status}"
        assert f"secret-{status}-body" not in str(excinfo.value)

    def test_onboard_404_raises_bot_missing_error(self):
        service = BcnService(_http_with_post_status(404))

        with pytest.raises(BcnBotMissingError) as excinfo:
            service.onboard_bot("b:e", "N", "D")

        assert "secret-404-body" not in str(excinfo.value)

    def test_onboard_503_raises_unavailable_error(self):
        service = BcnService(_http_with_post_status(503))

        with pytest.raises(BcnUnavailableError) as excinfo:
            service.onboard_bot("b:e", "N", "D")

        assert "secret-503-body" not in str(excinfo.value)

    def test_onboard_timeout_raises_typed_request_error(self):
        http = Mock(spec=HttpClient)
        http.post.side_effect = httpx.TimeoutException("read timeout")
        service = BcnService(http)

        with pytest.raises(BcnRequestError) as excinfo:
            service.onboard_bot("b:e", "N", "D")

        assert excinfo.value.kind == DeliveryFailure.UNKNOWN
        assert excinfo.value.code == "timeout"

    def test_onboard_unexpected_exception_raises_typed_request_error(self):
        http = Mock(spec=HttpClient)
        http.post.side_effect = RuntimeError("network adapter exploded")
        service = BcnService(http)

        with pytest.raises(BcnRequestError) as excinfo:
            service.onboard_bot("b:e", "N", "D")

        assert excinfo.value.kind == DeliveryFailure.UNKNOWN
        assert excinfo.value.code == "unknown"
        assert "network adapter exploded" in str(excinfo.value)


class TestCheckAdmissionTypedErrors:
    """check_admission 的 httpx 捕获点 → 类型化异常 (无 env 门控)。"""

    @pytest.mark.parametrize(
        "status,expected_type,expected_kind,expected_code",
        [
            (404, BcnBotMissingError, None, None),
            (503, BcnUnavailableError, None, None),
            (500, BcnRequestError, DeliveryFailure.UNKNOWN, "http_500"),
            (403, BcnRequestError, DeliveryFailure.REJECTED, "http_403"),
        ],
    )
    def test_check_admission_maps_status_to_typed_error(
        self, status, expected_type, expected_kind, expected_code,
    ):
        http = Mock(spec=HttpClient)
        http.get.return_value = httpx.Response(
            status,
            json={"error": f"secret-{status}-body"},
            request=httpx.Request(
                "GET", "https://example.com/bots/b:owner/admission"
            ),
        )
        service = BcnService(http)

        with pytest.raises(expected_type) as excinfo:
            service.check_admission(bot_uuid="b:owner", actor="human_88001")

        if expected_type is BcnRequestError:
            assert excinfo.value.kind == expected_kind
            assert excinfo.value.code == expected_code
        assert f"secret-{status}-body" not in str(excinfo.value)
        assert f"BCS admission HTTP error: {status}" in str(excinfo.value)


class TestGetAttributesMissingVsFailure:
    """get_attributes 缺失(404)与失败(500/超时)严格区分 (env=pre 真实链路)。"""

    @patch(_ENV_PATCH, return_value="pre")
    def test_get_attributes_404_raises_bot_missing_error(self, _mock_env):
        http = LocalHttpClient(base_url="http://fake-bcn:21000")
        service = BcnService(http_client=http, config=_TEST_BCN_CONFIG, timeout=5.0)
        http.set_response("get", _stub_response(404, {"error": "secret-no-such-bot"}))

        with pytest.raises(BcnBotMissingError) as excinfo:
            service.get_attributes(bot_uuid="b:x")

        assert "secret-no-such-bot" not in str(excinfo.value)

    @patch(_ENV_PATCH, return_value="pre")
    def test_get_attributes_500_raises_request_error_not_missing(self, _mock_env):
        http = LocalHttpClient(base_url="http://fake-bcn:21000")
        service = BcnService(http_client=http, config=_TEST_BCN_CONFIG, timeout=5.0)
        http.set_response("get", _stub_response(500, {"error": "secret-internal"}))

        with pytest.raises(BcnRequestError) as excinfo:
            service.get_attributes(bot_uuid="b:x")

        assert excinfo.value.kind == DeliveryFailure.UNKNOWN
        assert excinfo.value.code == "http_500"
        assert "secret-internal" not in str(excinfo.value)


class TestSingleSendTimeout:
    """单次发送超时:显式 ``timeout_seconds`` 覆盖本次请求,省略沿用默认。"""

    def _ok_post(self) -> Mock:
        http = Mock(spec=HttpClient)
        http.post.return_value = _post_response(
            200, {"bot_uuid": "b:e", "onboarded": True, "name": "N"},
        )
        return http

    def test_onboard_timeout_seconds_used_for_single_send(self):
        http = self._ok_post()
        service = BcnService(http, timeout=30.0)

        service.onboard_bot("b:e", "N", "D", timeout_seconds=7.5)

        assert http.post.call_args.kwargs["timeout"] == 7.5

    def test_onboard_omitted_timeout_uses_service_default(self):
        http = self._ok_post()
        service = BcnService(http, timeout=30.0)

        service.onboard_bot("b:e", "N", "D")

        assert http.post.call_args.kwargs["timeout"] == 30.0

    @patch(_ENV_PATCH, return_value="prod")
    def test_sync_caller_config_timeout_seconds_used_for_single_send(self, _mock_env):
        http = LocalHttpClient(base_url="http://fake-bcn:21000")
        service = BcnService(http_client=http, config=_TEST_BCN_CONFIG, timeout=5.0)
        http.set_response("patch", _stub_response(200, {"sync_status": "applied"}))

        service.sync_bot_caller_config(
            teamclaw_bot_uuid="20260502_1cjjh1ik",
            owner_workno="100000",
            enabled=True,
            source_revision=5,
            timeout_seconds=2.5,
        )

        assert http.calls_to("patch")[0].kwargs["timeout"] == 2.5

    @patch(_ENV_PATCH, return_value="pre")
    def test_get_attributes_timeout_seconds_used_for_single_send(self, _mock_env):
        http = LocalHttpClient(base_url="http://fake-bcn:21000")
        service = BcnService(http_client=http, config=_TEST_BCN_CONFIG, timeout=5.0)
        http.set_response("get", _stub_response(200, {"friend_ext": {}}))

        service.get_attributes(bot_uuid="b:x", timeout_seconds=1.5)

        assert http.calls_to("get")[0].kwargs["timeout"] == 1.5
