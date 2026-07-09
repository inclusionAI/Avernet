"""Tests for BaasService.restart_devices.

The method POSTs to the BaaS ``update-devices`` endpoint to restart specific
device instances (multi-instance scenario). These tests drive it through the
injected :class:`LocalHttpClient` (stub the response, assert the request) so
they exercise request shaping and the success / BaaS error / HTTP error branches
without any network.
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock

import httpx
import pytest

from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BaasServiceError,
)
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
class TestBaasServiceRestartDevices:
    def test_success_returns_publish_id(self):
        service, http = _make_service()
        http.set_response("post", _resp({"publish_id": 42}))

        result = service.restart_devices("BOT-1", ["DEVICE-001"], operator="staff-1")

        assert result["publish_id"] == 42

    def test_request_url_and_body_correct(self):
        service, http = _make_service()
        http.set_response("post", _resp({"publish_id": 7}))

        service.restart_devices(
            "BOT-1", ["DEVICE-001", "DEVICE-002"], operator="staff-1"
        )

        call = http.calls_to("post")[0]
        assert call.args[0] == "/api/v1/bots/BOT-1/update-devices"
        body = call.kwargs["json"]
        assert body["device_uuids"] == ["DEVICE-001", "DEVICE-002"]
        assert body["operator"] == "staff-1"
        assert body["auto_approve_publish"] is True
        assert 32 <= len(body["request_id"]) <= 64
        assert re.match(r"^[a-zA-Z0-9_-]+$", body["request_id"])
        assert call.kwargs["params"] == {"tenant": "tnt"}

    def test_baas_business_error_code_raises(self):
        service, http = _make_service()
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": 1, "message": "publish conflict"}
        http.set_response("post", response)

        with pytest.raises(BaasServiceError, match="publish conflict"):
            service.restart_devices("BOT-1", ["DEVICE-001"], operator="staff-1")

    def test_http_status_error_raises_baas_error(self):
        service, http = _make_service()
        err_response = MagicMock()
        err_response.status_code = 500
        err_response.text = "internal"
        response = MagicMock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=err_response
        )
        http.set_response("post", response)

        with pytest.raises(BaasServiceError, match="500"):
            service.restart_devices("BOT-1", ["DEVICE-001"], operator="staff-1")
