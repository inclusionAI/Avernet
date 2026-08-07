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


def _resp(data) -> MagicMock:
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"code": 0, "message": "ok", "data": data}
    return r


@pytest.mark.unit
class TestBaasServiceScaleBot:
    def test_scale_bot_success(self):
        service, http = _make_service()
        http.set_response("post", _resp({"bot_uuid": "BOT-1", "publish_id": 9, "target_count": 3}))

        result = service.scale_bot(
            bot_uuid="BOT-1",
            owner_id="op",
            request_id="req-1",
            target_count=3,
        )

        assert result["publish_id"] == 9
        assert result["target_count"] == 3
        call = http.calls_to("post")[0]
        assert call.args[0] == "/api/v1/bots/BOT-1/scale"
        assert call.kwargs["params"] == {"tenant": "tnt"}
        assert call.kwargs["json"] == {
            "target_count": 3,
            "operator": "op",
            "request_id": "req-1",
            "auto_approve_publish": False,
        }

    def test_scale_bot_sends_optional_image_config(self):
        service, http = _make_service()
        http.set_response("post", _resp({"bot_uuid": "BOT-1", "publish_id": 9}))

        service.scale_bot(
            bot_uuid="BOT-1",
            owner_id="op",
            request_id="req-1",
            target_count=3,
            config={
                "deploy_config": {"docker_image": "registry/arka:v2"},
            },
        )

        assert http.calls_to("post")[0].kwargs["json"]["config"] == {
            "deploy_config": {
                "docker_image": "registry/arka:v2",
            },
        }

    @pytest.mark.parametrize(
        ("bot_uuid", "owner_id", "request_id", "target_count", "match"),
        [
            ("", "op", "req-1", 3, "bot_uuid is required"),
            ("BOT-1", "", "req-1", 3, "owner_id is required"),
            ("BOT-1", "op", "", 3, "request_id is required"),
            ("BOT-1", "op", "req-1", 0, "target_count must be greater than 0"),
        ],
    )
    def test_scale_bot_invalid_args(self, bot_uuid, owner_id, request_id, target_count, match):
        service, _ = _make_service()
        with pytest.raises(BaasServiceError, match=match):
            service.scale_bot(
                bot_uuid=bot_uuid,
                owner_id=owner_id,
                request_id=request_id,
                target_count=target_count,
            )

    def test_scale_bot_http_status_error_passthrough(self):
        err_response = MagicMock()
        err_response.status_code = 500
        err_response.text = "internal"
        response = MagicMock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=err_response
        )

        service, http = _make_service()
        http.set_response("post", response)

        with pytest.raises(httpx.HTTPStatusError):
            service.scale_bot(
                bot_uuid="BOT-1",
                owner_id="op",
                request_id="req-1",
                target_count=3,
            )

    def test_scale_bot_business_error_wrapped_by_post_helper(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": 1, "message": "boom"}

        service, http = _make_service()
        http.set_response("post", response)

        with pytest.raises(BaasServiceError, match="boom"):
            service.scale_bot(
                bot_uuid="BOT-1",
                owner_id="op",
                request_id="req-1",
                target_count=3,
            )

    def test_scale_bot_http_client_send_hook_error_passthrough(self):
        service, http = _make_service()
        http.set_override(
            "post",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Error in httpx send hook")),
        )

        with pytest.raises(RuntimeError, match="Error in httpx send hook"):
            service.scale_bot(
                bot_uuid="BOT-1",
                owner_id="op",
                request_id="req-1",
                target_count=3,
            )


@pytest.mark.unit
class TestBaasServiceStopBot:
    def test_stop_bot_success(self):
        service, http = _make_service()
        http.set_response("post", _resp({"bot_uuid": "BOT-1", "publish_id": 10, "status": "STOPPING"}))

        result = service.stop_bot(
            bot_uuid="BOT-1",
            operator="op",
            request_id="req-stop-1",
            auto_approve_publish=False,
        )

        assert result["publish_id"] == 10
        assert result["status"] == "STOPPING"
        call = http.calls_to("post")[0]
        assert call.args[0] == "/api/v1/bots/BOT-1/stop"
        assert call.kwargs["params"] == {"tenant": "tnt"}
        assert call.kwargs["json"] == {
            "operator": "op",
            "request_id": "req-stop-1",
            "auto_approve_publish": False,
        }

    def test_stop_bot_auto_approve_publish(self):
        service, http = _make_service()
        http.set_response("post", _resp({"bot_uuid": "BOT-1", "publish_id": 11, "status": "STOPPING"}))

        result = service.stop_bot(
            bot_uuid="BOT-1",
            operator="op",
            request_id="req-stop-2",
            auto_approve_publish=True,
        )

        assert result["publish_id"] == 11
        call = http.calls_to("post")[0]
        assert call.args[0] == "/api/v1/bots/BOT-1/stop"
        assert call.kwargs["params"] == {"tenant": "tnt"}
        assert call.kwargs["json"]["auto_approve_publish"] is True

    def test_stop_bot_http_status_error_wrapped(self):
        err_response = MagicMock()
        err_response.status_code = 500
        err_response.text = "internal"
        response = MagicMock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=err_response
        )

        service, http = _make_service()
        http.set_response("post", response)

        with pytest.raises(BaasServiceError, match="BaaS API error: 500 - internal"):
            service.stop_bot(
                bot_uuid="BOT-1",
                operator="op",
                request_id="req-stop-3",
            )

    def test_stop_bot_business_error_wrapped_by_post_helper(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": 1, "message": "boom"}

        service, http = _make_service()
        http.set_response("post", response)

        with pytest.raises(BaasServiceError, match="boom"):
            service.stop_bot(
                bot_uuid="BOT-1",
                operator="op",
                request_id="req-stop-4",
            )


    def test_stop_bot_other_error_wrapped(self):
        service, http = _make_service()
        http.set_override(
            "post",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("send hook failed")),
        )

        with pytest.raises(BaasServiceError, match="Failed to stop bot in BaaS: send hook failed"):
            service.stop_bot(
                bot_uuid="BOT-1",
                operator="op",
                request_id="req-stop-5",
            )
