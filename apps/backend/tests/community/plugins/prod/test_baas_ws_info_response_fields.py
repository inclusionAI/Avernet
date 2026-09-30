"""BAAS ws_info response 必须暴露 paas_device_id / baas_base_url / engine_port 供 invoke-http 链路使用。"""
from unittest.mock import MagicMock

from agentclaw.community.plugins.local.http_client import LocalHttpClient


def test_get_ws_info_returns_paas_device_id_baas_base_url_engine_port():
    from agentclaw.community.core.service_bot.services.baas_service import BaasService

    # mock binding repo so we know device_id
    fake_binding = MagicMock()
    fake_binding.device_id = "container_abc--machine_M1--user_U7@tpl_42"

    fake_repo = MagicMock()
    fake_repo.get_by_id.return_value = fake_binding

    fake_response_json = {
        "code": 0,
        "data": {
            "ws_url": "wss://secbaas-prod.teamclaw.com/proxypass/foo/api/openclaw/ws",
            "token": "tok-xyz",
            "target": "foo",
            "expires_at": "2026-01-01T00:00:00Z",
        },
    }

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return fake_response_json

    http = LocalHttpClient(base_url="https://secbaas-prod.teamclaw.com")
    http.set_response("get", FakeResponse())

    svc = BaasService.__new__(BaasService)
    svc._baas_api_base = "https://secbaas-prod.teamclaw.com"
    svc._http = http
    svc._tenant = "team_claw"
    svc._device_binding_repo = fake_repo

    result = svc.get_ws_info(bind_id=42, port=20003)

    assert result.paas_device_id == "container_abc--machine_M1--user_U7@tpl_42"
    assert result.baas_base_url == "https://secbaas-prod.teamclaw.com"
    assert result.engine_port == 20003
