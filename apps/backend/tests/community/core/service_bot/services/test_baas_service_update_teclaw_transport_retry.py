"""Regression for the production ant_baselib send hook on TeClaw update.

Transport errors are reported by ``HttpxCallingException``, whose original
``httpx.RequestError`` is preserved on ``__cause__``. ``BaasService.update_teclaw_bot``
must model one safe retry because `request_id` is the idempotency key in the
BaaS ``UpdateBotRequest`` contract, while HTTP responses and unrelated actions
must retain the existing single-fail behavior."""
from __future__ import annotations

import httpx
import pytest
from unittest.mock import MagicMock

from agentclaw.community.core.service_bot.services.deploy.managed_composer import (
    ManagedDeployConfigComposer,
)
from tests.community.core.service_bot.services.deploy._noop_storage_policy import (
    NoopStoragePolicy,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService, BaasServiceError


class _MockWrapper(Exception):
    """The external ant_baselib wrapper shape, without depending on it."""
    pass


def _som_error_wrapper(cause: httpx.RequestError) -> Exception:
    wrapper = _MockWrapper("Error in httpx send hook")
    wrapper.__cause__ = cause
    return wrapper


def _svc_with_post_side_effect(side_effects) -> tuple[BaasService, MagicMock]:
    http_client = MagicMock()
    http_client.post.side_effect = side_effects
    return (
        BaasService(
            deploy_composer=ManagedDeployConfigComposer(
                storage_path=MagicMock(),
                sandbox_registry=MagicMock(),
                bot_repo=MagicMock(),
                storage_policy=NoopStoragePolicy(),
            ),
            startup_script_reader=MagicMock(**{"get_body.return_value": ""}),
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
        ),
        http_client,
    )


def _update_response() -> MagicMock:
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "code": 0,
        "message": "ok",
        "data": {"bot_uuid": "BOT-1", "publish_id": 7},
    }
    return response


def test_update_teclaw_bot_retries_idempotent_send_hook_transport_error():
    cause = httpx.RemoteProtocolError("Server disconnected")
    svc, http = _svc_with_post_side_effect([_som_error_wrapper(cause), _update_response()])

    result = svc.update_teclaw_bot(
        bot_uuid="BOT-1",
        bot={"bot_id": "b", "entity_id": "u"},
        owner_id="u",
        request_id="r" * 32,
        config_artifact={"schema_version": 3},
        template_uuid="tpl",
    )

    assert result["publish_id"] == 7
    assert http.post.call_count == 2
    first, second = http.post.call_args_list
    assert first == second
    assert first.args[0] == "/api/v1/bots/BOT-1/update"
    assert first.kwargs["json"]["request_id"] == "r" * 32


def test_update_teclaw_bot_never_retries_http_status_errors():
    request = httpx.Request("POST", "http://baas.test/api/v1/bots/BOT-1/update")
    response = httpx.Response(404, request=request, json={"detail": {"error_code": "BOT_NOT_FOUND"}})
    status_error = httpx.HTTPStatusError("404", request=request, response=response)
    svc, http = _svc_with_post_side_effect([status_error])

    with pytest.raises(BaasServiceError) as exc_info:
        svc.update_teclaw_bot(
            bot_uuid="BOT-1",
            bot={"bot_id": "b", "entity_id": "u"},
            owner_id="u",
            request_id="r" * 32,
            config_artifact={"schema_version": 3},
            template_uuid="tpl",
        )

    assert exc_info.value.response is not None
    assert http.post.call_count == 1


def test_update_teclaw_bot_retries_no_response_cause_and_preserves_single_request_id():
    cause = httpx.RemoteProtocolError("Server disconnected")
    wrapper = _som_error_wrapper(cause)
    svc, http = _svc_with_post_side_effect([wrapper, wrapper])

    with pytest.raises(Exception) as exc_info:
        svc.update_teclaw_bot(
            bot_uuid="BOT-1",
            bot={"bot_id": "b", "entity_id": "u"},
            owner_id="u",
            request_id="r" * 32,
            config_artifact={"schema_version": 3},
            template_uuid="tpl",
        )

    assert isinstance(exc_info.value, BaasServiceError)
    assert svc._request_error_from_tracing_wrapper(exc_info.value) is cause
    assert http.post.call_count == 2
    assert [c.args[0] for c in http.post.call_args_list] == [
        "/api/v1/bots/BOT-1/update",
        "/api/v1/bots/BOT-1/update",
    ]


def test_create_teclaw_bot_does_not_retry_transport_errors():
    cause = httpx.ConnectError("connection refused")
    svc, http = _svc_with_post_side_effect([_som_error_wrapper(cause)])

    with pytest.raises(BaasServiceError, match="create_teclaw_bot failed"):
        svc.create_teclaw_bot(
            bot={"bot_id": "b", "entity_id": "u"},
            owner_id="u",
            request_id="r" * 32,
            config_artifact={"schema_version": 3},
            template_uuid="tpl",
        )

    assert http.post.call_count == 1
