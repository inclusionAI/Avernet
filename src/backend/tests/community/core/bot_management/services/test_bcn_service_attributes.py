"""Tests for BcnService.get_attributes / patch_attributes.

读取 / 局部更新 BCN Provider Bot 协作属性 (``friend_ext`` / ``user_visibility``
/ ``friend_check_in_strategy``)。对应实现:
``agentclaw.community.core.bot_management.services.bcn_service.{get_attributes,
patch_attributes}``。

Both call the Provider admin API
(``GET/PATCH /providers/{provider_id}/bots/{bot_uuid}/attributes``) with only
``Authorization: Bearer {provider_admin_token}``; non prod/pre envs skip
(return ``{"skipped": True}``) exactly like ``register_provider_bot``. So these
tests mirror ``test_bcn_service_register_provider.py``'s fixture + coverage
style: a :class:`LocalHttpClient` stub (``set_response`` / ``set_override`` /
``calls_to``), ``get_current_env`` patched per case.
"""
from __future__ import annotations

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

# Down-link provider config the tests assert on (mirrors register_provider).
# provider_id_* are non-sensitive identifiers; the admin tokens are fake.
_TEST_BCN_CONFIG = BcnConfig(
    base_url="http://fake-bcn:21000",
    provider_id_prod="prv_4b7fce5b",
    provider_id_pre="prv_40354c8a",
    provider_admin_token_prod="test-bcn-token-prod",
    provider_admin_token_pre="test-bcn-token-pre",
)

_BOT_UUID = "bot-uuid-x"
_ATTR_PATH = "/providers/prv_40354c8a/bots/bot-uuid-x/attributes"
_PATCH_BODY = {"user_visibility": "private"}
_PATCH_ENV_PATCH = "agentclaw.community.core.bot_management.services.bcn_service.get_current_env"


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


def _raise(exc: Exception) -> Callable[..., object]:
    def _fn(*_args, **_kwargs):
        raise exc
    return _fn


class TestGetAttributes:
    """BCN 协作属性读接口 (Provider 管理 API GET)."""

    @patch(_PATCH_ENV_PATCH, return_value="dev")
    def test_dev_env_skipped_no_http_call(self, _mock_env, service, http):
        # dev env has no provider credentials → _get_provider_config returns
        # None → the skip branch (no HTTP, {"skipped": True}).
        result = service.get_attributes(bot_uuid=_BOT_UUID)

        assert result == {"skipped": True}
        assert http.calls_to("get") == []

    @patch(_PATCH_ENV_PATCH, return_value="pre")
    def test_pre_env_returns_attributes(self, _mock_env, service, http):
        http.set_response("get", _ok_response(200, {
            "friend_ext": {"x": 1},
            "user_visibility": "private",
        }))

        result = service.get_attributes(bot_uuid=_BOT_UUID)

        assert result == {"friend_ext": {"x": 1}, "user_visibility": "private"}
        call = http.calls_to("get")[0]
        assert call.args[0] == _ATTR_PATH
        assert call.kwargs["headers"]["Authorization"] == (
            "Bearer test-bcn-token-pre"
        )
        assert call.kwargs["timeout"] == 5.0

    @patch(_PATCH_ENV_PATCH, return_value="pre")
    def test_pre_env_non_dict_body_returns_empty(self, _mock_env, service, http):
        # response.json() returns a non-dict top-level → ``{}`` (the isinstance
        # guard's else), defensive against a malformed BCN payload.
        resp = Mock(status_code=200, text="")
        resp.raise_for_status.return_value = None
        resp.json.return_value = ["not", "a", "dict"]
        http.set_response("get", resp)

        result = service.get_attributes(bot_uuid=_BOT_UUID)

        assert result == {}

    @patch(_PATCH_ENV_PATCH, return_value="pre")
    def test_4xx_raises_bcn_service_error(self, _mock_env, service, http):
        http.set_response("get", _ok_response(400))

        with pytest.raises(BcnServiceError):
            service.get_attributes(bot_uuid=_BOT_UUID)

    @patch(_PATCH_ENV_PATCH, return_value="pre")
    def test_timeout_raises_bcn_service_error(self, _mock_env, service, http):
        http.set_override("get", _raise(httpx.TimeoutException("slow")))

        with pytest.raises(BcnServiceError):
            service.get_attributes(bot_uuid=_BOT_UUID)

    @patch(_PATCH_ENV_PATCH, return_value="pre")
    def test_unexpected_exception_raises_bcn_service_error(self, _mock_env, service, http):
        # 非 HTTPStatusError / TimeoutException 的异常走通用兜底分支。
        http.set_override("get", _raise(RuntimeError("network adapter exploded")))

        with pytest.raises(BcnServiceError) as excinfo:
            service.get_attributes(bot_uuid=_BOT_UUID)

        assert "network adapter exploded" in str(excinfo.value)


class TestPatchAttributes:
    """BCN 协作属性局部更新 (Provider 管理 API PATCH) — friend_ext 整体替换."""

    @patch(_PATCH_ENV_PATCH, return_value="dev")
    def test_dev_env_skipped_no_http_call(self, _mock_env, service, http):
        result = service.patch_attributes(bot_uuid=_BOT_UUID, body=_PATCH_BODY)

        assert result == {"skipped": True}
        assert http.calls_to("patch") == []

    @patch(_PATCH_ENV_PATCH, return_value="pre")
    def test_pre_env_patches_body_and_returns_response(self, _mock_env, service, http):
        http.set_response("patch", _ok_response(200, {"updated": True}))

        result = service.patch_attributes(bot_uuid=_BOT_UUID, body=_PATCH_BODY)

        assert result == {"updated": True}
        call = http.calls_to("patch")[0]
        assert call.args[0] == _ATTR_PATH
        assert call.kwargs["json"] == _PATCH_BODY
        assert call.kwargs["headers"]["Authorization"] == (
            "Bearer test-bcn-token-pre"
        )
        assert call.kwargs["headers"]["Content-Type"] == "application/json"

    @patch(_PATCH_ENV_PATCH, return_value="pre")
    def test_pre_env_non_dict_body_returns_empty(self, _mock_env, service, http):
        resp = Mock(status_code=200, text="")
        resp.raise_for_status.return_value = None
        resp.json.return_value = "a string, not a dict"
        http.set_response("patch", resp)

        result = service.patch_attributes(bot_uuid=_BOT_UUID, body=_PATCH_BODY)

        assert result == {}

    @patch(_PATCH_ENV_PATCH, return_value="pre")
    def test_4xx_raises_bcn_service_error(self, _mock_env, service, http):
        http.set_response("patch", _ok_response(400))

        with pytest.raises(BcnServiceError):
            service.patch_attributes(bot_uuid=_BOT_UUID, body=_PATCH_BODY)

    @patch(_PATCH_ENV_PATCH, return_value="pre")
    def test_timeout_raises_bcn_service_error(self, _mock_env, service, http):
        http.set_override("patch", _raise(httpx.TimeoutException("slow")))

        with pytest.raises(BcnServiceError):
            service.patch_attributes(bot_uuid=_BOT_UUID, body=_PATCH_BODY)

    @patch(_PATCH_ENV_PATCH, return_value="pre")
    def test_unexpected_exception_raises_bcn_service_error(self, _mock_env, service, http):
        http.set_override("patch", _raise(RuntimeError("network adapter exploded")))

        with pytest.raises(BcnServiceError) as excinfo:
            service.patch_attributes(bot_uuid=_BOT_UUID, body=_PATCH_BODY)

        assert "network adapter exploded" in str(excinfo.value)
