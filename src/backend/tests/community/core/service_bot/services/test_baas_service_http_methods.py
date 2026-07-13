"""Coverage for the remaining ``BaasService`` methods that issue HTTP.

These methods use the injected ``HttpClient`` (QUALIFIER_BAAS, base_url=baas gateway).
BaasService now passes **relative paths** (e.g. ``/api/v1/bots``) to the
baas-qualified client; ``invoke_http`` uses the general-qualified client with full
absolute URLs.  Each test drives the method through :class:`LocalHttpClient` stubs
(stub the response, assert the request) and checks the success path + the path / verb
used — no network.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.plugins.local.http_client import LocalHttpClient
from agentclaw.community.plugins.local.outbound_rules import NoopOutboundRuleProvider


def _make_service() -> tuple[BaasService, LocalHttpClient]:
    http = LocalHttpClient(base_url="http://baas.test")
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
        http_client=http,
        general_http_client=LocalHttpClient(base_url=""),
        outbound_rule_provider=NoopOutboundRuleProvider(),
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
    )
    return service, http


def _resp(data) -> MagicMock:
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"code": 0, "message": "ok", "data": data}
    return r


@pytest.mark.unit
class TestBaasServiceHttpMethods:
    def test_destroy_bot(self):
        service, http = _make_service()
        http.set_response("post", _resp({"bot_uuid": "BOT-1", "publish_id": 7}))
        result = service.destroy_bot("BOT-1", operator="op", request_id="req-1")
        assert result["publish_id"] == 7
        call = http.calls_to("post")[0]
        assert call.args[0] == "/api/v1/bots/BOT-1/destroy"
        assert call.kwargs["params"] == {"tenant": "tnt"}
        assert call.kwargs["json"] == {"operator": "op", "request_id": "req-1"}

    def test_restart_bot(self):
        service, http = _make_service()
        http.set_response("post", _resp({"bot_uuid": "BOT-1", "publish_id": 8}))
        result = service.restart_bot("BOT-1", operator="op", request_id="req-2")
        assert result["publish_id"] == 8
        assert http.calls_to("post")[0].args[0] == "/api/v1/bots/BOT-1/restart"

    def test_upgrade_bot_uses_explicit_template_uuid(self):
        service, http = _make_service()
        http.set_response("post", _resp({"bot_uuid": "BOT-1", "publish_id": 9}))

        result = service.upgrade_bot(
            bot_uuid="BOT-1",
            bot={
                "bot_id": "bot-1",
                "bot_name": "Bot 1",
                "entity_id": "user-1",
                "entity_type": "staff",
                "active_engine": "claude_code",
                "bot_type": "service",
            },
            owner_id="op",
            request_id="req-4",
            migration_path="/home/admin/nfs/bot-data/1/claude_code",
            template_uuid="TEMPLATE-claude-code",
        )

        assert result["publish_id"] == 9
        call = http.calls_to("post")[0]
        assert call.args[0] == "/api/v1/bots/BOT-1/update"
        assert call.kwargs["json"]["template_uuid"] == "TEMPLATE-claude-code"

    def test_get_publish_progress(self):
        service, http = _make_service()
        http.set_response("get", _resp({"status": "SUCCESS", "current_stage": "online"}))
        result = service.get_publish_progress(42, include_devices=True)
        assert result["status"] == "SUCCESS"
        call = http.calls_to("get")[0]
        assert call.args[0] == "/api/v1/publishes/42/progress"
        assert call.kwargs["params"]["tenant"] == "tnt"
        assert call.kwargs["params"]["include_devices"] == "true"

    def test_get_bot(self):
        service, http = _make_service()
        http.set_response("get", _resp({"bot_uuid": "BOT-1", "status": "RUNNING"}))
        result = service.get_bot("BOT-1")
        assert result["status"] == "RUNNING"
        call = http.calls_to("get")[0]
        assert call.args[0] == "/api/v1/bots/BOT-1"
        assert call.kwargs["params"] == {"tenant": "tnt"}

    def test_get_bot_with_health_check_and_engine_type(self):
        service, http = _make_service()
        http.set_response(
            "get", _resp({"bot_uuid": "BOT-1", "devices": [{"device_uuid": "D-1"}]})
        )
        result = service.get_bot(
            "BOT-1", health_check=True, engine_type="openclaw"
        )
        assert result["devices"][0]["device_uuid"] == "D-1"
        params = http.calls_to("get")[0].kwargs["params"]
        assert params["tenant"] == "tnt"
        assert params["health_check"] == "true"
        assert params["engine_type"] == "openclaw"

    def test_get_bot_health_check_false_omits_extra_params(self):
        service, http = _make_service()
        http.set_response("get", _resp({"bot_uuid": "BOT-1"}))
        service.get_bot("BOT-1", health_check=False, engine_type="")
        # backward compatible: no extra query keys when defaults
        assert http.calls_to("get")[0].kwargs["params"] == {"tenant": "tnt"}

    def test_list_bots(self):
        service, http = _make_service()
        http.set_response("get", _resp({"total": 2, "items": [{"bot_id": "a"}, {"bot_id": "b"}]}))
        total, items = service.list_bots(page=2, page_size=10, status="ACTIVE")
        assert total == 2
        assert [b["bot_id"] for b in items] == ["a", "b"]
        call = http.calls_to("get")[0]
        assert call.args[0] == "/api/v1/bots"
        assert call.kwargs["params"] == {
            "tenant": "tnt", "page": 2, "page_size": 10, "status": "ACTIVE",
        }

    def test_get_device_by_uuid(self):
        service, http = _make_service()
        http.set_response("get", _resp({"provider_device_id": "PD-9"}))
        result = service.get_device_by_uuid("DEVICE-9")
        assert result["provider_device_id"] == "PD-9"
        assert http.calls_to("get")[0].args[0] == "/api/v1/devices/DEVICE-9"

    def test_list_devices_by_bot_uuid(self):
        service, http = _make_service()
        http.set_response("get", _resp([{"items": [{"device_id": "d1"}]}]))
        items = service.list_devices_by_bot_uuid("BOT-1", tenant="other")
        assert items == [{"device_id": "d1"}]
        call = http.calls_to("get")[0]
        assert call.args[0] == "/api/v1/bots/BOT-1/devices"
        assert call.kwargs["params"] == {"tenant": "other"}
        assert call.kwargs["timeout"] == 30.0

    def test_list_devices_by_bot_uuid_uses_request_timeout(self):
        service, http = _make_service()
        http.set_response("get", _resp([{"items": []}]))

        service.list_devices_by_bot_uuid("BOT-1", timeout=8.0)

        assert http.calls_to("get")[0].kwargs["timeout"] == 8.0

    def test_approve_publish(self):
        service, http = _make_service()
        http.set_response("post", _resp({"status": "APPROVED"}))
        result = service.approve_publish(42, operator="op", request_id="req-3", comment="lgtm")
        assert result["status"] == "APPROVED"
        call = http.calls_to("post")[0]
        assert call.args[0] == "/api/v1/publishes/42/approve"
        assert call.kwargs["params"] == {"tenant": "tnt"}
        assert call.kwargs["json"] == {
            "operator": "op", "request_id": "req-3", "comment": "lgtm",
        }

    def test_update_device_outbound_rule(self):
        service, http = _make_service()
        http.set_response("put", _resp({}))
        rule = MagicMock()
        rule.header_operation_rules = []  # serialized to {"header_operation_rules": []}
        ok = service.update_device_outbound_rule("PAAS-1", rule)
        assert ok is True
        call = http.calls_to("put")[0]
        assert call.args[0] == "/api/v1/paas/devices/PAAS-1/outbound-rule"
        assert call.kwargs["json"] == {"header_operation_rules": []}

    def test_get_http_info_uses_http_client_port(self):
        """get_http_info 走 self._http.get 而非裸 httpx。

        验证策略：
        1. stub LocalHttpClient.get，成功路径下断言 calls_to("get") 命中（证明走了 port）。
        2. monkeypatch httpx.Client.__init__ 确保整个调用路径不触碰裸 httpx。
        """
        from agentclaw.community.core.service_bot.services.baas_service import HttpConnectionInfo
        import agentclaw.community.core.service_bot.services.baas_service as baas_mod

        service, http = _make_service()

        binding = MagicMock()
        binding.device_id = "DEVICE-42"
        service._device_binding_repo.get_by_id.return_value = binding

        http.set_response(
            "get",
            _resp({"http_url": "http://192.168.1.1:20010", "token": "tok-xyz", "target": "TECLAW_b@1:20010"}),
        )

        # 替换裸 httpx.Client：调用则爆炸，证明 get_http_info 不走裸 httpx
        with pytest.MonkeyPatch.context() as mp:
            def _forbidden(*a, **kw):
                raise AssertionError("get_http_info must not use bare httpx.Client")
            mp.setattr(baas_mod.httpx, "Client", _forbidden)

            result = service.get_http_info(bind_id=1, port=20010, path="/health")

        assert isinstance(result, HttpConnectionInfo)
        assert result.http_url == "http://192.168.1.1:20010"
        assert result.token == "tok-xyz"

        # 验证请求路径和参数（相对路径，base_url 由 baas-qualified client 拼接）
        call = http.calls_to("get")[0]
        assert call.args[0] == "/api/v1/bots/DEVICE-42/http-info"
        assert call.kwargs["params"]["tenant"] == "tnt"
        assert call.kwargs["params"]["port"] == 20010
        assert call.kwargs["params"]["path"] == "/health"
        assert "device_affinity" not in call.kwargs["params"]

    def test_get_http_info_with_device_affinity(self):
        """device_affinity 存在时应包含在 params 中。"""
        service, http = _make_service()
        binding = MagicMock()
        binding.device_id = "DEVICE-99"
        service._device_binding_repo.get_by_id.return_value = binding

        http.set_response(
            "get",
            _resp({"http_url": "http://10.0.0.1:20011", "token": "tok-affinity", "target": "TECLAW_b@1:20011"}),
        )
        result = service.get_http_info(bind_id=2, port=20011, device_affinity="sticky-node-1")
        assert result.token == "tok-affinity"

        call = http.calls_to("get")[0]
        assert call.kwargs["params"]["device_affinity"] == "sticky-node-1"

    def test_get_http_info_code_nonzero_raises(self):
        """BaaS 返回 code!=0 时应抛 BaasServiceError。"""
        from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

        service, http = _make_service()
        binding = MagicMock()
        binding.device_id = "DEVICE-1"
        service._device_binding_repo.get_by_id.return_value = binding

        err_resp = MagicMock()
        err_resp.raise_for_status.return_value = None
        err_resp.json.return_value = {"code": 404, "message": "device not found", "data": None}
        http.set_response("get", err_resp)

        with pytest.raises(BaasServiceError, match="device not found"):
            service.get_http_info(bind_id=1, port=20010)
