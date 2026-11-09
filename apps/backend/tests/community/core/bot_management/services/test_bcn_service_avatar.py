"""Task 2 BCN transport extensions: ``avatar_url`` passthrough payloads.

头像经既有 BCN transport 传递的 payload 规则 —— ``avatar_url`` 参数:
- 省略(默认 ``None``):不发送 ``avatar_url`` 键,与既有线上 payload 完全一致;
- 非空值:原样发送;
- 空字符串:仍然发送,由 BCN 侧解释为清空头像。

``None`` 在这个可省略的既有输入边界表示"不发送字段",不是领域缺失值。用例
以 ``Mock(spec=HttpClient)`` + 真实 :class:`httpx.Response` 驱动
:class:`BcnService`,断言真正发出的 JSON,不触网。

对应实现: ``bcn_service.onboard_bot`` / ``bcn_service.register_provider_bot``。
"""

from unittest.mock import Mock, patch

import httpx

from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.di.config import BcnConfig
from agentclaw.community.plugin_api.http_client import HttpClient

_ENV_PATCH = (
    "agentclaw.community.core.bot_management.services.bcn_service.get_current_env"
)


def _post_response(status: int, body: dict, path: str) -> httpx.Response:
    return httpx.Response(
        status,
        json=body,
        request=httpx.Request("POST", f"https://example.com{path}"),
    )


class TestOnboardBotAvatarPayload:
    """onboard_bot 的 omitted / set / clear 三态矩阵。"""

    def test_onboard_sends_explicit_avatar_clear(self):
        http = Mock(spec=HttpClient)
        http.post.return_value = httpx.Response(200,
            json={"bot_uuid": "b:e", "onboarded": True, "name": "N"},
            request=httpx.Request("POST", "https://example.com/admin/bots/onboard"))
        service = BcnService(http)
        service.onboard_bot("b:e", "N", "D", avatar_url="")
        assert http.post.call_args.kwargs["json"]["avatar_url"] == ""
        assert http.post.call_count == 1

    def test_onboard_omitted_avatar_sends_no_key(self):
        http = Mock(spec=HttpClient)
        http.post.return_value = _post_response(
            200,
            {"bot_uuid": "b:e", "onboarded": True, "name": "N"},
            "/admin/bots/onboard",
        )
        service = BcnService(http)

        service.onboard_bot("b:e", "N", "D")

        assert "avatar_url" not in http.post.call_args.kwargs["json"]
        assert http.post.call_count == 1

    def test_onboard_sends_set_avatar_url(self):
        http = Mock(spec=HttpClient)
        http.post.return_value = _post_response(
            200,
            {"bot_uuid": "b:e", "onboarded": True, "name": "N"},
            "/admin/bots/onboard",
        )
        service = BcnService(http)

        service.onboard_bot("b:e", "N", "D", avatar_url="https://cdn.example.com/a.png")

        assert (
            http.post.call_args.kwargs["json"]["avatar_url"]
            == "https://cdn.example.com/a.png"
        )
        assert http.post.call_count == 1


class TestRegisterProviderBotAvatarPayload:
    """register_provider_bot 的 omitted / set / clear 三态矩阵 (env=pre 真实链路)。"""

    def _service(self) -> tuple[BcnService, Mock]:
        http = Mock(spec=HttpClient)
        http.post.return_value = _post_response(
            200,
            {"bot_uuid": "u1", "provider_id": "prv_e", "provider_bot_ref": "b:100000"},
            "/providers/prv_e/bots",
        )
        service = BcnService(
            http,
            config=BcnConfig(
                provider_id="prv_e",
                provider_admin_token="test-bcn-token",
            ),
        )
        return service, http

    @patch(_ENV_PATCH, return_value="pre")
    def test_register_omitted_avatar_sends_no_key(self, _mock_env):
        service, http = self._service()

        service.register_provider_bot(
            teamclaw_bot_uuid="b",
            owner_workno="100000",
            name="Bot",
            summary="s",
        )

        assert "avatar_url" not in http.post.call_args.kwargs["json"]
        assert http.post.call_count == 1

    @patch(_ENV_PATCH, return_value="pre")
    def test_register_sends_set_avatar_url(self, _mock_env):
        service, http = self._service()

        service.register_provider_bot(
            teamclaw_bot_uuid="b",
            owner_workno="100000",
            name="Bot",
            summary="s",
            avatar_url="https://cdn.example.com/a.png",
        )

        assert (
            http.post.call_args.kwargs["json"]["avatar_url"]
            == "https://cdn.example.com/a.png"
        )
        assert http.post.call_count == 1

    @patch(_ENV_PATCH, return_value="pre")
    def test_register_sends_explicit_avatar_clear(self, _mock_env):
        service, http = self._service()

        service.register_provider_bot(
            teamclaw_bot_uuid="b",
            owner_workno="100000",
            name="Bot",
            summary="s",
            avatar_url="",
        )

        assert http.post.call_args.kwargs["json"]["avatar_url"] == ""
        assert http.post.call_count == 1
