"""Per-request proxy for unmatched paths on InMemoryDeviceAdapterTransport.

The transport mocks a well-known set of engine-adapter paths in memory
(health, capabilities, skills, layout probe, and ``/api/cron*``) and
proxies everything else to the adapter URL resolved per-request from
``conn_info`` — per-device, matching the production transport contract.
A ``default_adapter_url`` on the constructor serves as a safety net when
``conn_info`` carries no address. Without either, the original
``"unhandled path"`` sentinel applies (test deployments).
"""

from __future__ import annotations

import httpx
import pytest
from pytest import MonkeyPatch

from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTimeoutError,
)
from agentclaw.community.plugins.local.device_adapter_transport import (
    InMemoryDeviceAdapterTransport,
)

# ── Routing layer: cron guard + adapter URL resolution (invoke path) ──


@pytest.mark.asyncio
async def test_mocked_path_with_url_still_uses_memory():
    """A path the transport handles in memory (health) must never proxy,
    even when adapter resolution is possible."""
    conn_info = {"url": "http://127.0.0.1:1"}  # unroutable on purpose
    transport = InMemoryDeviceAdapterTransport()
    result = await transport.invoke(conn_info, "GET", "/health")
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_non_cron_without_url_returns_unhandled():
    """A path outside /api/cron with no conn_info address and no default
    → the 404 sentinel, identical to the original."""
    transport = InMemoryDeviceAdapterTransport()
    result = await transport.invoke({}, "GET", "/api/sessions/cron_001/messages")
    assert result["success"] is False
    assert "unhandled path" in result["message"]
    assert result["error_code"] == 404


@pytest.mark.asyncio
async def test_cron_guard_lets_cron_paths_into_memory_store():
    """/api/cron GET with conn_info url → the in-memory cron store serves it
    (the guard lets only cron paths through to the store, NOT to the proxy)."""
    conn_info = {"url": "http://127.0.0.1:20011"}
    transport = InMemoryDeviceAdapterTransport()
    result = await transport.invoke(conn_info, "GET", "/api/cron")
    assert result["data"] == []


@pytest.mark.asyncio
async def test_cron_guard_blocks_sessions_runs_from_fake_data():
    """/api/sessions/{sid}/runs without a URL: must return the 404 sentinel,
    NOT fabricated run_001 history (the cron shape parser must not eat it)."""
    transport = InMemoryDeviceAdapterTransport()
    result = await transport.invoke({}, "GET", "/api/sessions/cron_001/runs")
    assert result["success"] is False
    assert result["error_code"] == 404
    assert "unhandled path" in result["message"]


@pytest.mark.asyncio
async def test_cron_guard_blocks_models_from_leaking_cron_list():
    """/api/models without a URL: must return the 404 sentinel, not a
    fabricated cron list that leaks in-memory store contents."""
    transport = InMemoryDeviceAdapterTransport()
    # Seed the cron store to prove the guard works
    await transport.invoke({"url": "http://127.0.0.1:20011"}, "POST",
                           "/api/cron", body={"name": "test"})
    result = await transport.invoke({"uuid": "AU2"}, "GET", "/api/models")
    assert result["success"] is False
    assert result["error_code"] == 404
    assert "unhandled path" in result["message"]


# ── URL resolution from conn_info (per-request, per-device) ──


def test_resolve_adapter_url_prefers_conn_info_url():
    transport = InMemoryDeviceAdapterTransport(
        default_adapter_url="http://default.example.com"
    )
    resolved = transport._resolve_adapter_url({"url": "http://per-device.example.com"})
    assert resolved == "http://per-device.example.com"


def test_resolve_adapter_url_formats_target_as_http():
    transport = InMemoryDeviceAdapterTransport()
    resolved = transport._resolve_adapter_url({"target": "127.0.0.1:20012"})
    assert resolved == "http://127.0.0.1:20012"


def test_resolve_adapter_url_rejects_non_http_scheme():
    """Only full ``http(s)`` origins qualify; a value that merely starts
    with "http" (e.g. ``httpx://``) or a websocket URL falls through to
    the next resolution step instead of producing an unproxyable URL."""
    transport = InMemoryDeviceAdapterTransport(
        default_adapter_url="http://fallback.example.com"
    )
    assert (
        transport._resolve_adapter_url({"url": "httpx://not-a-site"})
        == "http://fallback.example.com"
    )
    via_target = transport._resolve_adapter_url(
        {"url": "ws://127.0.0.1:20003", "target": "127.0.0.1:20012"}
    )
    assert via_target == "http://127.0.0.1:20012"


def test_resolve_adapter_url_falls_back_to_default():
    transport = InMemoryDeviceAdapterTransport(
        default_adapter_url="http://fallback.example.com"
    )
    assert transport._resolve_adapter_url({}) == "http://fallback.example.com"


def test_resolve_adapter_url_none_when_no_resolution():
    transport = InMemoryDeviceAdapterTransport()
    assert transport._resolve_adapter_url({}) is None


# ── invoke() proxies non-cron paths with a resolvable URL ──


@pytest.mark.asyncio
async def test_invoke_proxies_and_returns_engine_json(monkeypatch: MonkeyPatch):
    """invoke() proxies a non-cron path with a resolvable URL and
    returns the engine adapter's response verbatim."""
    engine_response = {"success": True, "data": [], "message": None}

    async def mock_proxy(adapter_url, conn_info, method, path, body, params, timeout):
        assert path == "/api/sessions/cron_001/messages"
        return engine_response

    transport = InMemoryDeviceAdapterTransport(
        default_adapter_url="http://127.0.0.1:20003"
    )
    monkeypatch.setattr(transport, "_proxy", mock_proxy)
    result = await transport.invoke({}, "GET", "/api/sessions/cron_001/messages")
    assert result == engine_response


# ── _proxy() protocol error channel (raise, not return dict) ──


def _patch_httpx_async_client(monkeypatch: MonkeyPatch, handler) -> None:
    """Patch httpx.AsyncClient so any constructor within _proxy() gets a
    MockTransport wired to *handler*. Captures the pre-patch class to
    avoid self-referencing recursion."""
    original = httpx.AsyncClient

    def _client(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return original(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client)


@pytest.mark.asyncio
async def test_proxy_404_raises_endpoint_not_found(monkeypatch: MonkeyPatch):
    """A 404 from the adapter raises DeviceAdapterEndpointNotFoundError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not Found"})

    _patch_httpx_async_client(monkeypatch, handler)
    transport = InMemoryDeviceAdapterTransport(
        default_adapter_url="http://127.0.0.1:20003"
    )
    with pytest.raises(DeviceAdapterEndpointNotFoundError):
        await transport._proxy(
            "http://127.0.0.1:20003", {}, "GET", "/api/sessions/unknown",
            None, None, None,
        )


@pytest.mark.asyncio
async def test_proxy_http_error_raises_status_error(monkeypatch: MonkeyPatch):
    """A 422 from the adapter raises DeviceAdapterHTTPStatusError with the
    exact status_code preserved."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "bad session id"})

    _patch_httpx_async_client(monkeypatch, handler)
    transport = InMemoryDeviceAdapterTransport(
        default_adapter_url="http://127.0.0.1:20003"
    )
    with pytest.raises(DeviceAdapterHTTPStatusError) as exc_info:
        await transport._proxy(
            "http://127.0.0.1:20003", {}, "GET", "/api/sessions/cron_xxx",
            None, None, None,
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_proxy_timeout_raises_timeout_error(monkeypatch: MonkeyPatch):
    """A read timeout raises DeviceAdapterTimeoutError (the relay maps it
    to a 504-type timeout, not a generic 502)."""

    def raise_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated connection stall")

    _patch_httpx_async_client(monkeypatch, raise_timeout)
    transport = InMemoryDeviceAdapterTransport(
        default_adapter_url="http://127.0.0.1:20003"
    )
    with pytest.raises(DeviceAdapterTimeoutError):
        await transport._proxy(
            "http://127.0.0.1:20003", {}, "GET",
            "/api/sessions/cron_001/messages", None, None, None,
        )


@pytest.mark.asyncio
async def test_proxy_204_returns_wrapped_envelope(monkeypatch: MonkeyPatch):
    """A 204 (no content) returns a wrapped envelope dict — the JSON
    decode cannot crash on an empty body."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    _patch_httpx_async_client(monkeypatch, handler)
    transport = InMemoryDeviceAdapterTransport(
        default_adapter_url="http://127.0.0.1:20003"
    )
    result = await transport._proxy(
        "http://127.0.0.1:20003", {}, "GET", "/api/sessions",
        None, None, None,
    )
    assert result["success"] is True
    assert result["data"] is None


@pytest.mark.asyncio
async def test_proxy_200_json_returns_verbatim(monkeypatch: MonkeyPatch):
    """A 200 with a JSON payload returns the adapter response as-is."""
    engine_response = {"success": True, "data": [], "message": None}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=engine_response)

    _patch_httpx_async_client(monkeypatch, handler)
    transport = InMemoryDeviceAdapterTransport(
        default_adapter_url="http://127.0.0.1:20003"
    )
    result = await transport._proxy(
        "http://127.0.0.1:20003", {}, "GET",
        "/api/sessions/cron_001/messages", None, None, None,
    )
    assert result == engine_response


# ── Connection failures and per-device routing (invoke level) ──


@pytest.mark.asyncio
async def test_invoke_connect_refusal_raises_value_error(
    monkeypatch: MonkeyPatch,
):
    """A connection refusal surfaces as ValueError out of invoke() — the
    community-transport contract the relay maps uniformly — proving the
    guard propagates _proxy exceptions end to end."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _patch_httpx_async_client(monkeypatch, refuse)
    transport = InMemoryDeviceAdapterTransport(
        default_adapter_url="http://127.0.0.1:20003"
    )
    with pytest.raises(ValueError) as exc_info:
        await transport.invoke({}, "GET", "/api/sessions/cron_001/messages")
    assert "unreachable" in str(exc_info.value)


@pytest.mark.asyncio
async def test_invoke_routes_each_device_to_its_own_adapter(
    monkeypatch: MonkeyPatch,
):
    """Per-device routing proof, end to end: two devices with different
    addresses land on two different adapter origins, with the request
    path, query and per-device headers passed through verbatim
    (a single global fallback URL would fail this test)."""
    captured = []

    def capture(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"success": True, "data": [], "message": None})

    _patch_httpx_async_client(monkeypatch, capture)
    transport = InMemoryDeviceAdapterTransport()
    await transport.invoke(
        {"url": "http://127.0.0.1:9901/", "headers": {"x-proxypass-token": "tokenA"}},
        "GET",
        "/api/sessions/dev-1/messages",
        params={"limit": 2},
    )
    await transport.invoke(
        {"target": "127.0.0.1:9902"},
        "GET",
        "/api/sessions/dev-2/messages",
    )

    assert len(captured) == 2
    assert (
        str(captured[0].url)
        == "http://127.0.0.1:9901/api/sessions/dev-1/messages?limit=2"
    )
    assert captured[0].headers["x-proxypass-token"] == "tokenA"
    assert str(captured[1].url) == "http://127.0.0.1:9902/api/sessions/dev-2/messages"
    assert "x-proxypass-token" not in captured[1].headers
