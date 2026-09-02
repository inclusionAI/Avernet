"""Tests for ``BcnService.list_bots_by_task_modes``.

BBS 任务模式候选 roster 查询测试。
对应实现:
``agentclaw.community.core.bot_management.services.bcn_service.list_bots_by_task_modes``。

与 ``switch_bot`` / ``register_provider_bot`` / ``get_attributes`` 同套**统一 provider 身份**
(``BcnConfig`` prod/pre、``Authorization: Bearer {provider_admin_token}``、``provider_id`` 在 path),
访问 ``GET /providers/{provider_id}/bots/by-task-modes``。``BcnService`` 经注入的
:class:`HttpClient` 访问 BCN;测试用 :class:`LocalHttpClient`(stub 响应 + 断言调用)取代
patch ``httpx.Client``,与 ``test_bcn_service_switch_bot.py`` 同套手法。
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


# 下行 provider 配置(测试直接注入;生产来自 bcn yaml block 的 corp env overlay)。
# provider_id_* 为非敏感标识;admin token 为假值。
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


class TestListBotsByTaskModesEnvSelection:
    """复用统一 provider 身份: prod→prod 凭据, pre→pre 凭据。"""

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_prod_env_uses_prod_provider_credentials(self, _mock_env, service, http):
        http.set_response("get", _ok_response(200, {
            "success": True,
            "items": [
                {"bot_id": "bot-a", "task_claim_mode": True, "task_dream_mode": True},
                {"bot_id": "bot-b", "task_claim_mode": True, "task_dream_mode": False},
            ],
        }))

        items = service.list_bots_by_task_modes(claim=True, dream=True, match="all")

        assert [i["bot_id"] for i in items] == ["bot-a", "bot-b"]

        call = http.calls_to("get")[0]
        assert call.args[0] == "/providers/prv_4b7fce5b/bots/by-task-modes"
        assert call.kwargs["headers"]["Authorization"] == "Bearer test-bcn-token-prod"
        assert call.kwargs["timeout"] == 5.0
        assert call.kwargs["params"] == {
            "match": "all",
            "task_claim_mode": "true",
            "task_dream_mode": "true",
        }

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_pre_env_uses_pre_provider_credentials(self, _mock_env, service, http):
        http.set_response("get", _ok_response(200, {"items": [{"bot_id": "bot-pre"}]}))

        items = service.list_bots_by_task_modes(claim=True, dream=True, match="all")

        assert items == [{"bot_id": "bot-pre"}]

        call = http.calls_to("get")[0]
        assert call.args[0] == "/providers/prv_40354c8a/bots/by-task-modes"
        assert call.kwargs["headers"]["Authorization"] == "Bearer test-bcn-token-pre"


class TestListBotsByTaskModesRequestParams:
    """``claim`` / ``dream`` 为 ``None`` 时不下发对应 query; ``match`` 始终下发。"""

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_none_filters_omit_query_params(self, _mock_env, service, http):
        http.set_response("get", _ok_response(200, {"items": []}))

        service.list_bots_by_task_modes()

        call = http.calls_to("get")[0]
        assert call.kwargs["params"] == {"match": "any"}

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_false_filters_serialize_to_string_false(self, _mock_env, service, http):
        http.set_response("get", _ok_response(200, {"items": []}))

        service.list_bots_by_task_modes(claim=False, dream=False, match="all")

        call = http.calls_to("get")[0]
        assert call.kwargs["params"] == {
            "match": "all",
            "task_claim_mode": "false",
            "task_dream_mode": "false",
        }



    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_metadata_filters_are_forwarded_as_combined_query(self, _mock_env, service, http):
        http.set_response("get", _ok_response(200, {"items": [{"bot_id": "filtered"}]}))

        items = service.list_bots_by_task_modes(
            claim=True,
            dream=False,
            match="all",
            visibility=" public ",
            status="hidden",
            user_visibility="private",
        )

        assert items == [{"bot_id": "filtered"}]
        call = http.calls_to("get")[0]
        assert call.kwargs["params"] == {
            "match": "all",
            "task_claim_mode": "true",
            "task_dream_mode": "false",
            "visibility": "public",
            "status": "hidden",
            "user_visibility": "private",
        }

class TestListBotsByTaskModesResponseParsing:
    """解析 BCN 响应 ``items`` 列表,容错空 / 缺失 / 非列表。"""

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_returns_items_list_verbatim(self, _mock_env, service, http):
        items = [{"bot_id": "x"}, {"bot_id": "y"}]
        http.set_response("get", _ok_response(200, {"items": items}))

        assert service.list_bots_by_task_modes() == items

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_items_empty_returns_empty_list(self, _mock_env, service, http):
        http.set_response("get", _ok_response(200, {"items": []}))

        assert service.list_bots_by_task_modes() == []

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_missing_items_key_returns_empty_list(self, _mock_env, service, http):
        http.set_response("get", _ok_response(200, {"success": True}))

        assert service.list_bots_by_task_modes() == []

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_items_not_a_list_returns_empty_list(self, _mock_env, service, http):
        http.set_response("get", _ok_response(200, {"items": {"not": "a list"}}))

        assert service.list_bots_by_task_modes() == []


class TestListBotsByTaskModesErrorHandling:
    """未配置环境 / HTTP 错误 / 超时 / 意外异常 → ``BcnServiceError``。"""

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="dev",
    )
    def test_dev_env_not_configured_raises_and_skips_http(self, _mock_env, service, http):
        with pytest.raises(BcnServiceError) as excinfo:
            service.list_bots_by_task_modes()

        assert "not configured" in str(excinfo.value)
        assert http.calls_to("get") == []

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_4xx_raises_bcn_service_error(self, _mock_env, service, http):
        http.set_response("get", _ok_response(404))

        with pytest.raises(BcnServiceError):
            service.list_bots_by_task_modes()

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_5xx_raises_bcn_service_error(self, _mock_env, service, http):
        http.set_response("get", _ok_response(500))

        with pytest.raises(BcnServiceError):
            service.list_bots_by_task_modes()

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_timeout_raises_bcn_service_error(self, _mock_env, service, http):
        http.set_override("get", _raise(httpx.TimeoutException("slow")))

        with pytest.raises(BcnServiceError):
            service.list_bots_by_task_modes()

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="pre",
    )
    def test_unexpected_exception_wraps_bcn_service_error(self, _mock_env, service, http):
        http.set_override("get", _raise(RuntimeError("network adapter exploded")))

        with pytest.raises(BcnServiceError) as excinfo:
            service.list_bots_by_task_modes()

        assert "network adapter exploded" in str(excinfo.value)
