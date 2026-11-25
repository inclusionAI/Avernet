"""Generic updates preserve independent headers under BaaS append semantics."""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BaasServiceError,
)
from agentclaw.community.kernel.device_dto import HeaderOperationRule, OutBoundOperationRule

DEVICE = "ARCA-SANDBOX-test@0"
ROUTE = f"/api/v1/paas/devices/{DEVICE}/outbound-rule"


def _rule(header: str, value: str) -> OutBoundOperationRule:
    return OutBoundOperationRule(header_operation_rules=[
        HeaderOperationRule(domains=["mcp.test"], action="set", header_name=header, value=value),
    ])


def _service(client: httpx.Client) -> BaasService:
    # This boundary depends only on its injected HTTP client.
    service = BaasService.__new__(BaasService)
    service._http = client
    return service


@pytest.mark.unit
def test_owner_refresh_preserves_caller_and_replaces_latest_owner(caplog):
    # Model the upstream append contract confirmed by BaaS: matching rules replace,
    # unrelated rules remain. This is a client contract test, not BaaS implementation proof.
    caller = BaasService._outbound_rule_to_dict(_rule("x-caller-token", "caller-test-value"))
    stored = {(tuple(r["domains"]), r["header_name"], r["action"]): r
              for r in caller["header_operation_rules"]}
    requests = []

    def transport(request):
        requests.append(request)
        assert request.method == "PUT"
        assert request.url.path == ROUTE
        assert dict(request.url.params) == {"mode": "append"}
        assert request.extensions["timeout"]["read"] == 30.0
        payload = json.loads(request.content)
        for rule in payload["header_operation_rules"]:
            stored[(tuple(rule["domains"]), rule["header_name"], rule["action"])] = rule
        return httpx.Response(200, json={"code": 0, "message": "ok", "data": {}})

    caplog.set_level(logging.INFO)
    with httpx.Client(base_url="https://baas.test", transport=httpx.MockTransport(transport)) as client:
        service = _service(client)
        for value in ("owner-first-value", "owner-latest-value", "owner-latest-value"):
            assert service.update_device_outbound_rule(DEVICE, _rule("Authorization", value)) is True

    assert len(requests) == 3
    assert json.loads(requests[-1].content) == BaasService._outbound_rule_to_dict(
        _rule("Authorization", "owner-latest-value")
    )
    assert len(stored) == 2
    assert stored[(("mcp.test",), "x-caller-token", "set")]["value"] == "caller-test-value"
    assert stored[(("mcp.test",), "Authorization", "set")]["value"] == "owner-latest-value"
    assert "baas_outbound_update_requested" in caplog.text
    assert "baas_outbound_update_succeeded" in caplog.text
    assert "mode=append" in caplog.text
    assert DEVICE in caplog.text
    assert "status_code=200 result_code=0 duration_ms=" in caplog.text
    assert "Authorization" in caplog.text
    for secret in ("caller-test-value", "owner-first-value", "owner-latest-value"):
        assert secret not in caplog.text


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["http", "business", "timeout", "invalid-json"])
def test_failed_update_preserves_exception_contract_and_safe_logs(failure, caplog):
    secret = "sensitive-test-rule-value"

    def transport(request):
        if failure == "http":
            return httpx.Response(503, text=secret)
        if failure == "business":
            return httpx.Response(200, json={"code": 42, "message": secret})
        if failure == "invalid-json":
            return httpx.Response(200, text=secret)
        raise httpx.ReadTimeout(secret, request=request)

    caplog.set_level(logging.INFO)
    expected = {"http": BaasServiceError, "business": BaasServiceError,
                "timeout": httpx.ReadTimeout, "invalid-json": ValueError}[failure]
    with httpx.Client(base_url="https://baas.test", transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(expected):
            _service(client).update_device_outbound_rule(DEVICE, _rule("Authorization", secret))

    assert "baas_outbound_update_requested" in caplog.text
    assert "baas_outbound_update_failed" in caplog.text
    assert "baas_outbound_update_succeeded" not in caplog.text
    assert "mode=append" in caplog.text
    assert "error_type=" in caplog.text
    assert "duration_ms=" in caplog.text
    assert secret not in caplog.text


@pytest.mark.unit
def test_explicit_replace_retains_teclaw_global_contract():
    def transport(request):
        assert not request.url.query
        return httpx.Response(200, json={"code": 0})

    with httpx.Client(base_url="https://baas.test", transport=httpx.MockTransport(transport)) as client:
        assert _service(client).update_device_outbound_rule(
            DEVICE, _rule("Authorization", "test-value"), mode="replace"
        ) is True
