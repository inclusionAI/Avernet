"""Tests for BaasService.open_folder_bot.

The method POSTs to the BaaS ``open-folder`` endpoint; these tests drive it
through the injected :class:`LocalHttpClient` (stub the response, assert the
request) so they exercise request shaping and the success / BaaS error / HTTP
error / generic error branches without any network.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

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


@contextmanager
def _patched_client(response):
    """Patch httpx.Client so ``with httpx.Client() as c: c.post(...)`` yields
    a client whose ``post`` returns *response*."""
    client = MagicMock()
    client.post.return_value = response
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = False
    with patch(
        "agentclaw.community.core.service_bot.services.baas_service.httpx.Client",
        return_value=cm,
    ):
        yield client


@pytest.mark.unit
class TestOpenFolderBot:
    def test_success_with_folder_path(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": 0, "data": {"opened": True}}

        service, http = _make_service()
        http.set_response("post", response)
        result = service.open_folder_bot("bot-1", folder_path="/work/x")

        assert result == {"opened": True}
        # folder_path is forwarded in the JSON body; path is relative (base_url
        # lives on the client).
        call = http.calls_to("post")[0]
        assert call.kwargs["json"] == {"folder_path": "/work/x"}
        assert call.args[0] == "/api/v1/bots/tnt/bot-1/open-folder"

    def test_success_without_folder_path_sends_empty_body(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": 0, "data": {}}

        service, http = _make_service()
        http.set_response("post", response)
        result = service.open_folder_bot("bot-1")

        assert result == {}
        call = http.calls_to("post")[0]
        assert call.kwargs["json"] == {}

    def test_baas_business_error_code_raises(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": 1, "message": "boom"}

        service, http = _make_service()
        http.set_response("post", response)
        with pytest.raises(BaasServiceError, match="boom"):
            service.open_folder_bot("bot-1")

    def test_http_status_error_raises_baas_error(self):
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
            service.open_folder_bot("bot-1")

    def test_generic_exception_wrapped_as_baas_error(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.side_effect = ValueError("bad json")

        service, http = _make_service()
        http.set_response("post", response)
        with pytest.raises(BaasServiceError, match="Failed to open folder"):
            service.open_folder_bot("bot-1")
