"""The teclaw POST error path must preserve the HTTP payload for structured
extraction (found by the cross-publish DI suite, C4).

The BaaS server answers a gone bot with 404
``{"detail": {"error_code": "BOT_NOT_FOUND", ...}}``. `_post_teclaw` converts
the ``httpx.HTTPStatusError`` to ``BaasServiceError`` — and used to drop the
response, so ``BotBuildService._extract_baas_error_info`` (which reads
``.response.json()``) could never classify BOT_NOT_FOUND on the teclaw path:
the upgrade first-release fallback and the restart recreate leg only worked
for ARCA (whose ``upgrade_bot`` re-raises the HTTPStatusError intact)."""
import httpx
import pytest
from unittest.mock import MagicMock

from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BaasServiceError,
)


def _svc_with_404(detail: dict) -> BaasService:
    request = httpx.Request("POST", "http://baas.test/api/v1/bots/BOT-gone/update")
    response = httpx.Response(404, request=request, json={"detail": detail})
    err = httpx.HTTPStatusError("404", request=request, response=response)
    http_resp = MagicMock()
    http_resp.raise_for_status.side_effect = err
    http_client = MagicMock()
    http_client.post.return_value = http_resp

    return BaasService(
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
        http_client=http_client,
        general_http_client=MagicMock(),
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
        outbound_rule_provider=MagicMock(),
    )


def test_post_teclaw_error_carries_response_payload():
    detail = {"error_code": "BOT_NOT_FOUND", "message": "Bot not found: BOT-gone"}
    svc = _svc_with_404(detail)

    with pytest.raises(BaasServiceError) as exc_info:
        svc.update_teclaw_bot(
            bot_uuid="BOT-gone",
            bot={"bot_id": "b", "entity_id": "u"},
            owner_id="u",
            request_id="r" * 32,
            config_artifact={"schema_version": 3},
            template_uuid="tpl",
        )

    err = exc_info.value
    # The structured payload survives the normalization — this is what lets
    # BotBuildService._extract_baas_error_info classify BOT_NOT_FOUND and take
    # the gone-bot fallback on the teclaw path.
    assert err.response is not None
    assert err.response.json()["detail"] == detail
    assert "404" in str(err)
