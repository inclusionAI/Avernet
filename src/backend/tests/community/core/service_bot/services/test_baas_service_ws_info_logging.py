"""Log-level coverage for BaasService.get_ws_info on HTTP errors.

get_ws_info re-raises every HTTP error as BaasServiceError, and all callers
(connection handler, device plugin, identity) catch and degrade. The common
cases — 404 BOT_NOT_FOUND (bot released/expired) and 503 NO_ACTIVE_DEVICES
(device still coming up) — are expected, self-healing business states, NOT
faults. They must be logged at WARNING, never ERROR, so they don't flood the
alarm/ticket pipeline. These tests pin that contract.

The module logger is a SOFAPy logger (propagate=False, own handlers), so caplog
can't see it — assert on the logger method called instead, which is what we
actually care about: warning() yes, error() no.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BaasServiceError,
)


def _make_service_raising(status_code: int, body: str) -> BaasService:
    """BaasService whose injected http_client.get(...).raise_for_status() raises
    an httpx.HTTPStatusError — the path get_ws_info classifies as an HTTP error.
    """
    binding = MagicMock()
    binding.device_id = "BOT-xyz"
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = binding

    request = httpx.Request("GET", "http://baas.test/api/v1/bots/BOT-xyz/ws-info")
    response = httpx.Response(status_code, request=request, text=body)
    err = httpx.HTTPStatusError(f"{status_code}", request=request, response=response)
    http_resp = MagicMock()
    http_resp.raise_for_status.side_effect = err
    http_client = MagicMock()
    http_client.get.return_value = http_resp

    return BaasService(
        baas_api_base="http://baas.test",
        tenant="tnt",
        template_uuid="tpl",
        bot_repo=MagicMock(),
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(),
        storage_path=MagicMock(),
        device_binding_repo=binding_repo,
        default_ttl_minutes=10080,
        sandbox_registry=MagicMock(),
        http_client=http_client,
        general_http_client=MagicMock(),
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
        outbound_rule_provider=MagicMock(),
    )


def _ws_info_logged_calls(spy: MagicMock, level: str) -> list:
    """get_ws_info log calls at the given level (warning/error)."""
    return [
        c for c in getattr(spy, level).call_args_list
        if c.args and "get_ws_info" in str(c.args[0])
    ]


@pytest.mark.unit
class TestGetWsInfoErrorLogging:
    def test_404_bot_not_found_logs_warning_not_error(self):
        body = (
            '{"detail":{"error":"BOT_NOT_FOUND",'
            '"message":"Bot not found","bot_uuid":"BOT-xyz"}}'
        )
        service = _make_service_raising(404, body)
        with patch(
            "agentclaw.community.core.service_bot.services.baas_service.logger"
        ) as spy:
            with pytest.raises(BaasServiceError):
                service.get_ws_info(bind_id=1)

        assert _ws_info_logged_calls(spy, "warning"), "404 should log at WARNING"
        assert not _ws_info_logged_calls(spy, "error"), "404 must NOT log at ERROR"

    def test_503_no_active_devices_logs_warning_not_error(self):
        body = (
            '{"detail":{"error":"NO_ACTIVE_DEVICES",'
            '"message":"No active devices available"}}'
        )
        service = _make_service_raising(503, body)
        with patch(
            "agentclaw.community.core.service_bot.services.baas_service.logger"
        ) as spy:
            with pytest.raises(BaasServiceError):
                service.get_ws_info(bind_id=1)

        assert _ws_info_logged_calls(spy, "warning"), "503 should log at WARNING"
        assert not _ws_info_logged_calls(spy, "error"), "503 must NOT log at ERROR"

    def test_still_raises_baas_service_error(self):
        """Behavior unchanged: the HTTP error is still surfaced to callers."""
        service = _make_service_raising(500, "boom")
        with pytest.raises(BaasServiceError):
            service.get_ws_info(bind_id=1)
