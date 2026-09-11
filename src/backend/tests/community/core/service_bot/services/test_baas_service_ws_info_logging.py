"""Log-level coverage for BaasService.get_ws_info on HTTP errors.

get_ws_info keeps every HTTP failure inside the BaasServiceError hierarchy.
Structured NO_ACTIVE_DEVICES is a normal external waiting state and logs at
INFO; transport/5xx failures are transient and log at WARNING; malformed or
unexpected failures keep the ERROR path. These tests pin that classification.

The module logger is a SOFAPy logger (propagate=False, own handlers), so caplog
can't see it — assert on the logger method called instead, which is what we
actually care about: warning() yes, error() no.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentclaw.community.core.service_bot.services.deploy.managed_composer import (
    ManagedDeployConfigComposer,
)
from agentclaw.community.core.service_bot.services.baas_service import (
    BaasNoActiveDevicesError,
    BaasService,
    BaasServiceError,
    BaasTransientServiceError,
)


def _make_service_raising(
    status_code: int, body: str, headers: dict[str, str] | None = None
) -> BaasService:
    """BaasService whose injected http_client.get(...).raise_for_status() raises
    an httpx.HTTPStatusError — the path get_ws_info classifies as an HTTP error.

    ``headers`` seeds the raising response's headers (e.g. a gateway ``Location``
    on a 3xx redirect) so tests can assert what the warning log surfaces.
    """
    binding = MagicMock()
    binding.device_id = "BOT-xyz"
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = binding

    request = httpx.Request("GET", "http://baas.test/api/v1/bots/BOT-xyz/ws-info")
    response = httpx.Response(
        status_code, request=request, text=body, headers=headers or {}
    )
    err = httpx.HTTPStatusError(f"{status_code}", request=request, response=response)
    http_resp = MagicMock()
    http_resp.raise_for_status.side_effect = err
    http_client = MagicMock()
    http_client.get.return_value = http_resp

    return BaasService(
        deploy_composer=ManagedDeployConfigComposer(
            storage_path=MagicMock(),
            sandbox_registry=MagicMock(),
            bot_repo=MagicMock(),
        ),
        startup_script_reader=MagicMock(**{"get_body.return_value": ""}),
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
    """get_ws_info log calls at the requested level."""
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

    @pytest.mark.parametrize("status_code", [404, 503])
    def test_no_active_devices_is_structured_and_logs_info(
        self, status_code: int
    ) -> None:
        body = (
            '{"detail":{"error":"NO_ACTIVE_DEVICES",'
            '"message":"No active devices available"}}'
        )
        service = _make_service_raising(status_code, body)
        with patch(
            "agentclaw.community.core.service_bot.services.baas_service.logger"
        ) as spy:
            with pytest.raises(BaasNoActiveDevicesError) as raised:
                service.get_ws_info(bind_id=1)

        assert raised.value.status_code == status_code
        assert raised.value.error_code == "NO_ACTIVE_DEVICES"
        assert _ws_info_logged_calls(spy, "info")
        assert not _ws_info_logged_calls(spy, "warning")
        assert not _ws_info_logged_calls(spy, "error")

    @pytest.mark.parametrize(
        ("status_code", "body"),
        [
            (404, '{"detail":{"error":"BOT_NOT_FOUND"}}'),
            (403, '{"detail":{"error":"NO_ACTIVE_DEVICES"}}'),
            (500, '{"detail":{"error":"NO_ACTIVE_DEVICES"}}'),
            (503, '{"detail":{"error":"SOME_OTHER_ERROR"}}'),
            (503, "not-json"),
        ],
    )
    def test_other_http_errors_do_not_become_device_offline(
        self, status_code: int, body: str
    ) -> None:
        service = _make_service_raising(status_code, body)

        with pytest.raises(BaasServiceError) as raised:
            service.get_ws_info(bind_id=1)

        assert not isinstance(raised.value, BaasNoActiveDevicesError)

    def test_still_raises_baas_service_error(self):
        """Behavior unchanged: the HTTP error is still surfaced to callers."""
        service = _make_service_raising(500, "boom")
        with pytest.raises(BaasTransientServiceError):
            service.get_ws_info(bind_id=1)

    def test_transport_timeout_is_transient_and_logs_warning(self) -> None:
        service = _make_service_raising(500, "unused")
        service._http.get.side_effect = httpx.ReadTimeout("timeout")

        with patch(
            "agentclaw.community.core.service_bot.services.baas_service.logger"
        ) as spy:
            with pytest.raises(BaasTransientServiceError):
                service.get_ws_info(bind_id=1)

        assert _ws_info_logged_calls(spy, "warning")
        assert not _ws_info_logged_calls(spy, "error")

    def test_302_redirect_logs_location_and_correlation_fields(self):
        """A gateway 302 (Spanner) must surface the redirect ``Location`` plus
        bot_uuid/tenant/device_affinity in the WARNING log, so intermittent
        redirects can be clustered and the redirect target inspected.
        """
        body = (
            '<html><head><title>302 Found</title></head>'
            '<body><h1>302 Found</h1><hr/>Powered by Spanner</body></html>'
        )
        location = "https://login.example.com/sso?back=/api/v1/bots"
        service = _make_service_raising(302, body, headers={"Location": location})
        with patch(
            "agentclaw.community.core.service_bot.services.baas_service.logger"
        ) as spy:
            with pytest.raises(BaasServiceError):
                service.get_ws_info(bind_id=1, device_affinity="500207")

        warnings = _ws_info_logged_calls(spy, "warning")
        assert warnings, "302 should log at WARNING"
        assert not _ws_info_logged_calls(spy, "error"), "302 must NOT log at ERROR"
        logged = str(warnings[0].args[0])
        assert location in logged, "redirect Location must be in the warning log"
        assert "bot_uuid=BOT-xyz" in logged
        assert "device_affinity=500207" in logged
