"""Caller overlays preserve the original session query and redact credentials."""

import logging
from unittest.mock import MagicMock

import httpx
import pytest

from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BaasServiceError,
)
from agentclaw.community.kernel.device_dto import (
    HeaderOperationRule,
    OutBoundOperationRule,
)


def _service(response):
    service = object.__new__(BaasService)
    service._http = MagicMock()
    service._http.put.return_value = response
    return service


def _rule():
    return OutBoundOperationRule(
        header_operation_rules=[
            HeaderOperationRule(
                domains=["example.com"],
                action="set",
                header_name="x-caller-token",
                value="opaque-sensitive-caller",
            )
        ]
    )


def test_session_append_encodes_once_and_logs_safely(caplog):
    key = "agent:中文 A/&+?%"
    response = httpx.Response(
        200,
        json={
            "code": 0,
            "data": {"message": "ok", "nested": {"Authorization": "secret-response"}},
        },
        request=httpx.Request("PUT", "http://baas.test"),
    )
    service = _service(response)
    with caplog.at_level(logging.INFO):
        assert service.append_caller_outbound_rule(
            "device@42", _rule(), session_key=key
        )
    args, kwargs = service._http.put.call_args
    assert args == ("/api/v1/paas/devices/device@42/outbound-rule",)
    assert kwargs["params"] == {"mode": "append", "session_key": key}
    assert (
        kwargs["json"]["header_operation_rules"][0]["value"]
        == "opaque-sensitive-caller"
    )
    assert "caller_outbound_append_started" in caplog.text
    assert "caller_outbound_append_succeeded" in caplog.text
    assert "example.com" in caplog.text and "message" in caplog.text
    for secret in (key, "opaque-sensitive-caller", "secret-response"):
        assert secret not in caplog.text


@pytest.mark.parametrize("status", [404, 503, 500])
def test_session_append_failed_response_is_safe(status, caplog):
    response = httpx.Response(
        status,
        json={"business": "unavailable", "secret": "secret-response"},
        request=httpx.Request("PUT", "http://baas.test?session_key=secret-session"),
    )
    service = _service(response)
    with caplog.at_level(logging.INFO), pytest.raises(BaasServiceError) as error:
        service.append_caller_outbound_rule(
            "device@42", _rule(), session_key="secret-session"
        )
    assert error.value.__cause__ is None
    assert "caller_outbound_append_failed" in caplog.text
    assert "unavailable" in caplog.text
    assert "secret-session" not in caplog.text
    assert "secret-response" not in caplog.text
    assert "opaque-sensitive-caller" not in caplog.text


@pytest.mark.parametrize(
    "body", [{"code": 42, "data": {"token": "nested-sensitive"}}, []]
)
def test_append_business_error_and_invalid_json_object(body, caplog):
    service = _service(
        httpx.Response(200, json=body, request=httpx.Request("PUT", "http://baas.test"))
    )
    with caplog.at_level(logging.INFO), pytest.raises(BaasServiceError):
        service.append_caller_outbound_rule("device@42", _rule())
    assert "caller_outbound_append_failed" in caplog.text
    assert "nested-sensitive" not in caplog.text


def test_append_non_json_response_fails_safely(caplog):
    service = _service(
        httpx.Response(
            200,
            text="opaque-sensitive-caller",
            request=httpx.Request("PUT", "http://baas.test"),
        )
    )
    with caplog.at_level(logging.INFO), pytest.raises(BaasServiceError):
        service.append_caller_outbound_rule("device@42", _rule())
    assert "non_json" in caplog.text
    assert "opaque-sensitive-caller" not in caplog.text


def test_append_transport_exception_has_no_credential_chain(caplog):
    service = _service(None)
    service._http.put.side_effect = httpx.ReadTimeout(
        "secret-session opaque-sensitive-caller"
    )
    with caplog.at_level(logging.INFO), pytest.raises(BaasServiceError) as error:
        service.append_caller_outbound_rule(
            "device@42", _rule(), session_key="secret-session"
        )
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__
    assert "ReadTimeout" in caplog.text
    assert "secret-session" not in caplog.text
    assert "opaque-sensitive-caller" not in caplog.text


def test_real_httpx_client_does_not_log_session_url(caplog):
    from urllib.parse import quote

    key = "private:中文/session&key"
    with httpx.Client(
        base_url="http://baas.test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"code": 0})
        ),
    ) as client:
        service = object.__new__(BaasService)
        service._http = client
        with caplog.at_level(logging.INFO):
            assert service.append_caller_outbound_rule(
                "device@42", _rule(), session_key=key
            )
    assert key not in caplog.text
    assert quote(key, safe="") not in caplog.text
    assert "private%3A" not in caplog.text
