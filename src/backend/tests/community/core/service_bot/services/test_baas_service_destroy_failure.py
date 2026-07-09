"""Failure-branch tests for BaasService.destroy_bot.

destroy_bot POSTs to the BaaS ``destroy`` endpoint; on a non-200 HTTP status,
a non-zero BaaS business code, or any other client error it must not crash —
it surfaces a structured BaasServiceError instead of returning success. These
tests drive it through the injected :class:`LocalHttpClient` (stub the response,
assert the request) so the failure handling is exercised without any network.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BaasServiceError,
)
from agentclaw.community.plugins.local.http_client import LocalHttpClient


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
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
        outbound_rule_provider=MagicMock(),
    )
    return service, http


@pytest.mark.unit
class TestDestroyBotFailure:
    def test_http_status_error_returns_structured_failure_not_success(self):
        err_response = MagicMock()
        err_response.status_code = 500
        err_response.text = "internal"
        response = MagicMock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=err_response
        )

        service, http = _make_service()
        http.set_response("post", response)
        with pytest.raises(BaasServiceError, match="500"):
            service.destroy_bot("bot-1", operator="op", request_id="req-123456")

    def test_baas_business_error_code_returns_structured_failure(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": 1, "message": "boom"}

        service, http = _make_service()
        http.set_response("post", response)
        with pytest.raises(BaasServiceError, match="boom"):
            service.destroy_bot("bot-1", operator="op", request_id="req-123456")

    def test_generic_exception_wrapped_as_baas_error(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.side_effect = ValueError("bad json")

        service, http = _make_service()
        http.set_response("post", response)
        with pytest.raises(BaasServiceError, match="Failed to destroy bot"):
            service.destroy_bot("bot-1", operator="op", request_id="req-123456")