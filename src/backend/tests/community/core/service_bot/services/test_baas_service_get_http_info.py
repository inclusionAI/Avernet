"""Tests for BaasService.get_http_info — resolve container HTTP connection info.

Migrated from bare ``patch("httpx.Client", ...)`` to LocalHttpClient stub after
get_http_info was refactored to use self._http (the injected HttpClient port).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BaasServiceError,
    HttpConnectionInfo,
)
from agentclaw.community.plugins.local.http_client import LocalHttpClient


@pytest.fixture
def fake_binding_repo():
    """Fake DeviceBindingRepository returning a record with device_id."""
    repo = MagicMock()
    binding = MagicMock()
    binding.device_id = "bot-uuid-001"
    repo.get_by_id.return_value = binding
    return repo


@pytest.fixture
def http():
    return LocalHttpClient(base_url="http://baas.fake")


@pytest.fixture
def baas_service(fake_binding_repo, http):
    """BaasService wired with LocalHttpClient; only device_binding_repo is meaningfully used by get_http_info."""
    return BaasService(
        baas_api_base="http://baas.fake",
        tenant="team_claw",
        template_uuid="tpl",
        bot_repo=MagicMock(),
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(),
        storage_path=MagicMock(),
        device_binding_repo=fake_binding_repo,
        default_ttl_minutes=10080,
        sandbox_registry=MagicMock(),
        http_client=http,
        general_http_client=LocalHttpClient(base_url=""),
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
        outbound_rule_provider=MagicMock(),
    )


def _ok(data: dict) -> MagicMock:
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"code": 0, "data": data}
    return r


def _err_resp(status_code: int, json_body: dict) -> MagicMock:
    """Simulate an HTTP error response that raise_for_status will throw."""
    r = MagicMock()
    r.status_code = status_code
    r.text = str(json_body)
    err = httpx.HTTPStatusError(
        message=f"{status_code}",
        request=httpx.Request("GET", "http://baas.fake/x"),
        response=httpx.Response(
            status_code=status_code,
            json=json_body,
            request=httpx.Request("GET", "http://baas.fake/x"),
        ),
    )
    r.raise_for_status.side_effect = err
    return r


def test_get_http_info_happy_path(baas_service, http):
    """200 OK with valid data -> HttpConnectionInfo(http_url, token)."""
    http.set_response(
        "get",
        _ok({
            "http_url": "http://10.0.0.1:20010",
            "token": "abc",
            "target": "TECLAW_b_teclaw1@4:20010",
        }),
    )

    info = baas_service.get_http_info(
        bind_id=42,
        port=20010,
        path="/api/file/read",
        device_affinity="entity-001",
    )

    assert isinstance(info, HttpConnectionInfo)
    assert info.http_url == "http://10.0.0.1:20010"
    assert info.token == "abc"
    assert info.target == "TECLAW_b_teclaw1@4:20010"

    call = http.calls_to("get")[0]
    assert call.args[0] == "/api/v1/bots/bot-uuid-001/http-info"
    params = call.kwargs["params"]
    assert params["tenant"] == "team_claw"
    assert params["port"] == 20010
    assert params["path"] == "/api/file/read"
    assert params["device_affinity"] == "entity-001"


def test_get_http_info_binding_not_found_raises(baas_service, fake_binding_repo):
    """binding repo returns None -> raise BaasServiceError."""
    fake_binding_repo.get_by_id.return_value = None

    with pytest.raises(BaasServiceError, match="Device binding not found"):
        baas_service.get_http_info(
            bind_id=999, port=20010, path="/x"
        )


def test_get_http_info_404_raises(baas_service, http):
    """Container/BaaS returns 404 -> raise BaasServiceError."""
    http.set_response("get", _err_resp(404, {"error": "bot_not_found", "message": "Bot not found"}))

    with pytest.raises(BaasServiceError, match="404"):
        baas_service.get_http_info(bind_id=42, port=20010, path="/x")


def test_get_http_info_503_raises(baas_service, http):
    """No active devices -> raise BaasServiceError."""
    http.set_response("get", _err_resp(503, {"error": "no_active_devices", "message": "No active devices"}))

    with pytest.raises(BaasServiceError, match="503"):
        baas_service.get_http_info(bind_id=42, port=20010, path="/x")


def test_get_http_info_business_code_nonzero_raises(baas_service, http):
    """200 OK but business code != 0 -> raise BaasServiceError with message."""
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"code": -1, "message": "internal lookup failed"}
    http.set_response("get", r)

    with pytest.raises(BaasServiceError, match="internal lookup failed"):
        baas_service.get_http_info(bind_id=42, port=20010, path="/x")


def test_get_http_info_network_error_raises(baas_service, http):
    """Transport error -> wrapped as BaasServiceError."""
    def _boom(*a, **kw):
        raise httpx.ConnectError("connection refused")

    http.set_override("get", _boom)

    with pytest.raises(BaasServiceError, match="Failed to get http info"):
        baas_service.get_http_info(bind_id=42, port=20010, path="/x")


def test_get_http_info_uses_self_tenant_when_not_specified(baas_service, http):
    """tenant param omitted -> uses self._tenant."""
    http.set_response("get", _ok({"http_url": "http://x:1", "token": "t", "target": "TECLAW_b@1:1"}))

    baas_service.get_http_info(bind_id=42, port=20010, path="/x")

    assert http.calls_to("get")[0].kwargs["params"]["tenant"] == "team_claw"


# ── device_uuid 透传(多实例 service bot) ───────────────────────────────


def test_get_http_info_device_uuid_included_in_params(baas_service, http):
    """传 device_uuid 时,BaaS GET /http-info 的 query params 含 device_uuid。"""
    http.set_response(
        "get",
        _ok({"http_url": "http://10.0.0.1:20010", "token": "abc", "target": "T:20010"}),
    )

    baas_service.get_http_info(
        bind_id=42,
        port=20010,
        path="/api/file/read",
        device_uuid="DEV-xyz",
    )

    params = http.calls_to("get")[0].kwargs["params"]
    assert params["device_uuid"] == "DEV-xyz"
    # 既有字段不受影响
    assert params["tenant"] == "team_claw"
    assert params["port"] == 20010
    assert params["path"] == "/api/file/read"


def test_get_http_info_device_uuid_omitted_when_none(baas_service, http):
    """不传 / 传 None 时,query params 不含 device_uuid key(向后兼容单实例)."""
    http.set_response(
        "get",
        _ok({"http_url": "http://10.0.0.1:20010", "token": "abc", "target": "T:20010"}),
    )

    baas_service.get_http_info(bind_id=42, port=20010, path="/x")

    params = http.calls_to("get")[0].kwargs["params"]
    assert "device_uuid" not in params


def test_get_http_info_device_uuid_alongside_device_affinity(baas_service, http):
    """device_uuid 与 device_affinity 同时传递时,两者都进 params(对称、互不干扰)."""
    http.set_response(
        "get",
        _ok({"http_url": "http://10.0.0.1:20010", "token": "abc", "target": "T:20010"}),
    )

    baas_service.get_http_info(
        bind_id=42,
        port=20010,
        path="/api/file/read",
        device_affinity="entity-001",
        device_uuid="DEV-xyz",
    )

    params = http.calls_to("get")[0].kwargs["params"]
    assert params["device_affinity"] == "entity-001"
    assert params["device_uuid"] == "DEV-xyz"
