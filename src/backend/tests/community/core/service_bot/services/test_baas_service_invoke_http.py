"""Tests for BaasService.invoke_http — unified container-internal API entry point.

TDD red-green cycle:
  1. Run before implementation → all tests red (AttributeError or NotImplementedError)
  2. Add invoke_http to BaasService → all tests green

Architecture note:
  - ``get_http_info`` is a control-plane call (→ BaaS gateway) and uses ``self._http``
    (baas-qualified HttpClient, base_url=baas gateway), passing a relative path.
  - The actual container request uses ``self._general_http`` (general-qualified
    HttpClient, base_url=""), passing the full absolute http_url from BaaS.
  Tests use two separate LocalHttpClient instances: ``baas_http`` for the gateway
  call (get_http_info) and ``general_http`` for the container call.
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


# ── Fixtures ──────────────────────────────────────────────────────────────────

BASE_URL = "http://baas.fake"
DEVICE_ID = "bot-uuid-001"

# Matches the task spec exactly:
# http_url = base_url + /api/v1/bots/t/u/invoke-http/20003/api/file/read
INVOKE_HTTP_REL = "/api/v1/bots/t/u/invoke-http/20003/api/file/read"
INVOKE_HTTP_FULL = BASE_URL + INVOKE_HTTP_REL
TOKEN = "tok-xyz"


@pytest.fixture
def fake_binding_repo():
    repo = MagicMock()
    binding = MagicMock()
    binding.device_id = DEVICE_ID
    repo.get_by_id.return_value = binding
    return repo


@pytest.fixture
def baas_http():
    """Baas-qualified HttpClient (base_url=baas gateway) — used by get_http_info."""
    return LocalHttpClient(base_url=BASE_URL)


@pytest.fixture
def general_http():
    """General-qualified HttpClient (base_url="") — used by invoke_http container call."""
    return LocalHttpClient(base_url="")


@pytest.fixture
def http(baas_http):
    """Alias for legacy compatibility within this module; refers to the baas client."""
    return baas_http


@pytest.fixture
def baas_service(fake_binding_repo, baas_http, general_http):
    return BaasService(
        baas_api_base=BASE_URL,
        tenant="team_claw",
        template_uuid="tpl",
        bot_repo=MagicMock(),
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(),
        storage_path=MagicMock(),
        device_binding_repo=fake_binding_repo,
        default_ttl_minutes=10080,
        sandbox_registry=MagicMock(),
        http_client=baas_http,
        general_http_client=general_http,
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
        outbound_rule_provider=MagicMock(),
    )


def _http_info_ok(http_url: str, token: str) -> MagicMock:
    """Stub for get_http_info BaaS call."""
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"code": 0, "data": {"http_url": http_url, "token": token, "target": "TECLAW_b@1:20003"}}
    return r


def _container_ok(status_code: int = 200) -> MagicMock:
    """Stub for the actual container-side POST."""
    r = MagicMock()
    r.status_code = status_code
    r.raise_for_status.return_value = None
    return r


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_invoke_http_passes_full_url_and_posts(baas_service, baas_http, general_http):
    """invoke_http resolves http_url via baas client, passes the full URL to general client."""
    # Stub: GET on baas client for get_http_info (control-plane → BaaS gateway)
    baas_http.set_response("get", _http_info_ok(INVOKE_HTTP_FULL, TOKEN))
    # Stub: POST on general client for actual container API (full http_url)
    general_http.set_response("post", _container_ok())

    resp = baas_service.invoke_http(
        bind_id=42,
        port=20003,
        path="/api/file/read",
    )

    assert resp.status_code == 200

    # get_http_info goes through baas_http (GET)
    get_calls = baas_http.calls_to("get")
    assert len(get_calls) == 1

    # Container POST goes through general_http (full absolute URL)
    post_calls = general_http.calls_to("post")
    assert len(post_calls) == 1

    posted_path = post_calls[0].args[0]
    assert posted_path == INVOKE_HTTP_FULL, (
        f"invoke_http must POST the full http_url via general_http, got: {posted_path!r}"
    )


def test_invoke_http_sends_openclawToken_header(baas_service, baas_http, general_http):
    """invoke_http attaches openclawToken from HttpConnectionInfo."""
    baas_http.set_response("get", _http_info_ok(INVOKE_HTTP_FULL, TOKEN))
    general_http.set_response("post", _container_ok())

    baas_service.invoke_http(
        bind_id=42,
        port=20003,
        path="/api/file/read",
    )

    post_call = general_http.calls_to("post")[0]
    headers = post_call.kwargs.get("headers", {})
    assert headers.get("openclawToken") == TOKEN


def test_invoke_http_auth_header_override(baas_service, baas_http, general_http):
    """auth_header overrides the token's header name (no openclawToken).

    Links to the agentclawproxy /proxypass gateway path (teclaw/arca): the token
    must go under ``x-proxypass-token``, not the secbaas tunnel's openclawToken.
    """
    baas_http.set_response("get", _http_info_ok(INVOKE_HTTP_FULL, TOKEN))
    general_http.set_response("post", _container_ok())

    baas_service.invoke_http(
        bind_id=42,
        port=20003,
        path="/api/file/list",
        auth_header="x-proxypass-token",
    )

    headers = general_http.calls_to("post")[0].kwargs.get("headers", {})
    assert headers.get("x-proxypass-token") == TOKEN
    assert "openclawToken" not in headers


def test_invoke_http_http_url_equals_base_url_passes_as_is(baas_service, baas_http, general_http):
    """When http_url == base_url, the full URL is passed through unchanged."""
    baas_http.set_response("get", _http_info_ok(BASE_URL, TOKEN))
    general_http.set_response("post", _container_ok())

    baas_service.invoke_http(
        bind_id=42,
        port=20003,
        path="/healthz",
    )

    post_calls = general_http.calls_to("post")
    assert len(post_calls) == 1
    posted_path = post_calls[0].args[0]
    # Full URL passed through as-is
    assert posted_path == BASE_URL


def test_invoke_http_always_passes_full_url(baas_service, baas_http, general_http):
    """invoke_http always passes http_url as-is regardless of whether it matches base_url."""
    other_url = "http://other-host:9999/api/file/read"
    baas_http.set_response("get", _http_info_ok(other_url, TOKEN))
    general_http.set_response("post", _container_ok())

    baas_service.invoke_http(
        bind_id=42,
        port=20003,
        path="/api/file/read",
    )

    post_call = general_http.calls_to("post")[0]
    assert post_call.args[0] == other_url


def test_invoke_http_default_method_is_post(baas_service, baas_http, general_http):
    """Default method is POST."""
    baas_http.set_response("get", _http_info_ok(INVOKE_HTTP_FULL, TOKEN))
    general_http.set_response("post", _container_ok())

    baas_service.invoke_http(bind_id=42, port=20003, path="/api/file/read")

    assert len(general_http.calls_to("post")) == 1
    assert len(baas_http.calls_to("get")) == 1  # only the http-info GET


def test_invoke_http_get_method(baas_service, baas_http, general_http):
    """method='GET' should route through general_http.get (not post)."""
    baas_http.set_response("get", _http_info_ok(INVOKE_HTTP_FULL, TOKEN))
    general_http.set_response("get", _container_ok())

    baas_service.invoke_http(
        bind_id=42,
        port=20003,
        path="/api/file/read",
        method="GET",
    )

    # baas_http: only the get_http_info GET
    assert len(baas_http.calls_to("get")) == 1
    # general_http: the container GET
    assert len(general_http.calls_to("get")) == 1
    assert len(general_http.calls_to("post")) == 0


def test_invoke_http_passes_json_body(baas_service, baas_http, general_http):
    """json kwarg is forwarded to the underlying POST."""
    baas_http.set_response("get", _http_info_ok(INVOKE_HTTP_FULL, TOKEN))
    general_http.set_response("post", _container_ok())

    payload = {"file_path": "/tmp/foo.py"}
    baas_service.invoke_http(
        bind_id=42,
        port=20003,
        path="/api/file/read",
        json=payload,
    )

    post_call = general_http.calls_to("post")[0]
    assert post_call.kwargs.get("json") == payload


def test_invoke_http_passes_files_and_data_multipart(baas_service, baas_http, general_http):
    """invoke_http(files=, data=) forwards multipart to general_http.post; json empty."""
    baas_http.set_response("get", _http_info_ok(INVOKE_HTTP_FULL, TOKEN))
    general_http.set_response("post", _container_ok())

    files = {"file": ("foo.py", b"print(1)")}
    data = {"path": "/tmp/foo.py"}
    baas_service.invoke_http(
        bind_id=42,
        port=20003,
        path="/api/file/write",
        files=files,
        data=data,
    )

    post_call = general_http.calls_to("post")[0]
    assert post_call.args[0] == INVOKE_HTTP_FULL
    assert post_call.kwargs.get("files") == files
    assert post_call.kwargs.get("data") == data
    assert post_call.kwargs.get("json") is None
    assert post_call.kwargs.get("headers", {}).get("openclawToken") == TOKEN


def test_invoke_http_returns_httpx_response(baas_service, baas_http, general_http):
    """Return value is the raw httpx.Response from general_http."""
    container_resp = _container_ok(200)
    baas_http.set_response("get", _http_info_ok(INVOKE_HTTP_FULL, TOKEN))
    general_http.set_response("post", container_resp)

    result = baas_service.invoke_http(
        bind_id=42,
        port=20003,
        path="/api/file/read",
    )

    assert result is container_resp


def test_invoke_http_passes_tenant_and_device_affinity_to_http_info(baas_service, baas_http, general_http):
    """tenant and device_affinity are forwarded to get_http_info (→ BaaS GET params on baas_http)."""
    baas_http.set_response("get", _http_info_ok(INVOKE_HTTP_FULL, TOKEN))
    general_http.set_response("post", _container_ok())

    baas_service.invoke_http(
        bind_id=42,
        port=20003,
        path="/api/file/read",
        tenant="custom_tenant",
        device_affinity="entity-xyz",
    )

    get_call = baas_http.calls_to("get")[0]
    params = get_call.kwargs.get("params", {})
    assert params.get("tenant") == "custom_tenant"
    assert params.get("device_affinity") == "entity-xyz"


def test_invoke_http_always_passes_full_url_evil_host(baas_service, baas_http, general_http):
    """任何 http_url（含 evil host）都整段透传 general_http——调用方负责 BaaS 侧域名校验。"""
    evil_url = "http://baas.fake.evil.com/x"
    baas_http.set_response("get", _http_info_ok(evil_url, TOKEN))
    general_http.set_response("post", _container_ok())

    baas_service.invoke_http(bind_id=42, port=20003, path="/x")

    post_call = general_http.calls_to("post")[0]
    # 完整 URL 原样透传（BaaS 侧已校验合法域名，客户端不再截取）
    assert post_call.args[0] == evil_url


def test_invoke_http_put_method_passthrough(baas_service, baas_http, general_http):
    """method='PUT' 应调用 general_http.put，携带完整 URL、json body 和 token。"""
    baas_http.set_response("get", _http_info_ok(INVOKE_HTTP_FULL, TOKEN))
    general_http.set_response("put", _container_ok(200))

    payload = {"key": "value"}
    resp = baas_service.invoke_http(
        bind_id=42,
        port=20003,
        path="/api/file/read",
        method="PUT",
        json=payload,
    )

    assert resp.status_code == 200
    put_calls = general_http.calls_to("put")
    assert len(put_calls) == 1
    put_call = put_calls[0]
    assert put_call.args[0] == INVOKE_HTTP_FULL
    assert put_call.kwargs.get("json") == payload
    assert put_call.kwargs.get("headers", {}).get("openclawToken") == TOKEN


def test_invoke_http_delete_method_passthrough(baas_service, baas_http, general_http):
    """method='DELETE' 应调用 general_http.delete，携带完整 URL 和 token，不带 json body。"""
    baas_http.set_response("get", _http_info_ok(INVOKE_HTTP_FULL, TOKEN))
    general_http.set_response("delete", _container_ok(204))

    resp = baas_service.invoke_http(
        bind_id=42,
        port=20003,
        path="/api/file/read",
        method="DELETE",
    )

    assert resp.status_code == 204
    delete_calls = general_http.calls_to("delete")
    assert len(delete_calls) == 1
    delete_call = delete_calls[0]
    assert delete_call.args[0] == INVOKE_HTTP_FULL
    assert delete_call.kwargs.get("headers", {}).get("openclawToken") == TOKEN


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
def test_invoke_http_passes_params_to_container_call(method, baas_service, baas_http, general_http):
    """params 透传到容器调用——四种 method 都应把 params 传给 general_http。"""
    baas_http.set_response("get", _http_info_ok(INVOKE_HTTP_FULL, TOKEN))
    general_http.set_response(method.lower(), _container_ok())

    query = {"q": "v", "n": "1"}
    baas_service.invoke_http(
        bind_id=42,
        port=20003,
        path="/api/file/read",
        method=method,
        params=query,
    )

    call = general_http.calls_to(method.lower())[0]
    assert call.kwargs.get("params") == query


# ── device_uuid 透传(多实例 service bot) ───────────────────────────────


def test_invoke_http_passes_device_uuid_to_get_http_info(baas_service, baas_http, general_http):
    """invoke_http(device_uuid=...) 把 device_uuid 透传给 get_http_info(→ BaaS /http-info query)."""
    baas_http.set_response("get", _http_info_ok(INVOKE_HTTP_FULL, TOKEN))
    general_http.set_response("post", _container_ok())

    baas_service.invoke_http(
        bind_id=42,
        port=20003,
        path="/api/file/read",
        device_uuid="DEV-xyz",
    )

    # get_http_info 的 GET 走 baas_http,params 里应有 device_uuid
    get_call = baas_http.calls_to("get")[0]
    params = get_call.kwargs.get("params", {})
    assert params.get("device_uuid") == "DEV-xyz"


def test_invoke_http_default_device_uuid_none(baas_service, baas_http, general_http):
    """不传 device_uuid 时 get_http_info params 不含 device_uuid key(向后兼容)."""
    baas_http.set_response("get", _http_info_ok(INVOKE_HTTP_FULL, TOKEN))
    general_http.set_response("post", _container_ok())

    baas_service.invoke_http(
        bind_id=42,
        port=20003,
        path="/api/file/read",
    )

    params = baas_http.calls_to("get")[0].kwargs.get("params", {})
    assert "device_uuid" not in params
