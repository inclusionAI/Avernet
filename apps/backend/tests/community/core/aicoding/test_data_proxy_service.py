"""Tests for :class:`DataProxyService` — core layer.

OCB's data-proxy is a pure transparent forwarder; container selection
is the upstream proxypass's job. The tests cover:

1. **resolve_engine_base** tiered resolution
   (``AICODING_ENGINE_URL`` env → ``SERVER_ENV=dev/local`` localhost
   default → :class:`EngineUrlNotConfigured`).
2. **forward** — happy path drives outbound method/URL/body/headers
   correctly; httpx network failure surfaces as
   :class:`EngineUnreachable`; hop-by-hop headers stripped both ways.

``httpx.AsyncClient`` is monkey-patched to a recording stub so the
focus stays on forwarding shape without a real engine adapter.
Route-level concerns (FastAPI signature, HTTP status mapping) live in
the endpoint test file.
"""
from __future__ import annotations

from typing import Any, Dict

import httpx
import pytest

from agentclaw.community.core.aicoding.services import data_proxy_service as dps
from agentclaw.community.core.aicoding.services.data_proxy_service import (
    DataProxyService,
    EngineUnreachable,
    EngineUrlNotConfigured,
    ForwardResult,
    StreamingForwardResult,
)


# ─────────────────────────────────────────────────────────────────────────────
# Stubs
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def service() -> DataProxyService:
    return DataProxyService()


class _FakeResponse:
    """Stands in for an ``httpx.Response`` opened with ``stream=True``.

    Supports the surface the service touches: ``status_code`` / ``headers``
    plus the streaming lifecycle (``aread`` / ``aiter_raw`` / ``aclose``).
    Pass ``chunks`` to drive the SSE path; otherwise the whole body comes
    back from a single ``aread``.
    """

    def __init__(self, status_code, headers, content=b"", *, chunks=None):
        self.status_code = status_code
        self.headers = httpx.Headers(headers)
        self._content = content
        self._chunks = chunks
        self.closed = False

    async def aread(self):
        return self._content

    async def aiter_raw(self):
        for chunk in (self._chunks if self._chunks is not None else [self._content]):
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.fixture
def stub_upstream(monkeypatch):
    """Replace ``httpx.AsyncClient`` with a recording stub.

    The service opens every request as a stream
    (``build_request`` + ``send(stream=True)``) so the buffer-vs-stream
    decision can key off the response content-type. The stub records the
    outbound ``build_request`` kwargs under ``calls`` (same keys the old
    ``request`` stub used) and the constructor kwargs (incl. ``timeout``)
    under ``client_kwargs``.
    """
    captured: Dict[str, Any] = {
        "calls": [],
        "client_kwargs": [],
        "client_closed": False,
        "response": _FakeResponse(
            200, {"content-type": "application/json"}, b'{"ok": true}',
        ),
    }

    class _StubAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["client_kwargs"].append(kwargs)

        def build_request(self, **kwargs):
            captured["calls"].append(kwargs)
            return kwargs

        async def send(self, request, *, stream=False):
            resp = captured["response"]
            if isinstance(resp, Exception):
                raise resp
            return resp

        async def aclose(self):
            captured["client_closed"] = True

    monkeypatch.setattr(dps.httpx, "AsyncClient", _StubAsyncClient)
    return captured


# ─────────────────────────────────────────────────────────────────────────────
# resolve_engine_base — tiered
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveEngineBase:
    def test_tier1_env_override_wins(self, monkeypatch, service):
        monkeypatch.setenv(dps.ENGINE_URL_ENV, "http://override:9999/")
        assert service.resolve_engine_base() == "http://override:9999"

    def test_tier2_dev_falls_back_to_local_default(
        self, monkeypatch, service,
    ):
        monkeypatch.delenv(dps.ENGINE_URL_ENV, raising=False)
        monkeypatch.setenv("SERVER_ENV", "dev")
        assert service.resolve_engine_base() == dps.LOCAL_DEFAULT_ENGINE_URL

    def test_tier2_local_falls_back_to_local_default(
        self, monkeypatch, service,
    ):
        monkeypatch.delenv(dps.ENGINE_URL_ENV, raising=False)
        monkeypatch.setenv("SERVER_ENV", "local")
        assert service.resolve_engine_base() == dps.LOCAL_DEFAULT_ENGINE_URL

    def test_prod_without_env_var_raises(self, monkeypatch, service):
        """Production deployments must set ``AICODING_ENGINE_URL`` — without
        it (and SERVER_ENV not dev/local) the resolver gives up."""
        monkeypatch.delenv(dps.ENGINE_URL_ENV, raising=False)
        monkeypatch.setenv("SERVER_ENV", "prod")
        with pytest.raises(EngineUrlNotConfigured) as exc_info:
            service.resolve_engine_base()
        assert exc_info.value.op == "data_proxy_resolve"


# ─────────────────────────────────────────────────────────────────────────────
# forward
# ─────────────────────────────────────────────────────────────────────────────


class TestForward:
    @pytest.fixture(autouse=True)
    def _tier1_pin(self, monkeypatch):
        """Tier-1 pinned so forward tests have a deterministic engine
        base regardless of the host's SERVER_ENV."""
        monkeypatch.setenv(dps.ENGINE_URL_ENV, "http://engine.test:20003")
        yield

    @pytest.mark.parametrize(
        "method",
        ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    @pytest.mark.asyncio
    async def test_verb_forwards_to_engine_data_path(
        self, service, stub_upstream, method,
    ):
        """Per-verb happy path — mirrors the endpoint-level ``ExpectSuccess``
        matrix in ``tests/endpoints/test_aicoding_data_proxy.py``. Asserts
        the catch-all threads the verb to ``httpx`` unchanged and the
        upstream response is fully assembled into :class:`ForwardResult`
        (status / content / media_type)."""
        result = await service.forward(
            subpath="api/health", method=method,
            headers={}, query_string="", body=b"",
        )
        # ForwardResult assembly — mirrors data_proxy_service.py:155-160
        # (the ``return ForwardResult(...)`` tail).
        assert isinstance(result, ForwardResult)
        assert result.status_code == 200
        assert result.content == b'{"ok": true}'
        assert result.media_type == "application/json"
        # Verb + URL placed on the wire correctly.
        call = stub_upstream["calls"][0]
        assert call["method"] == method
        assert call["url"] == "http://engine.test:20003/data/api/health"

    @pytest.mark.asyncio
    async def test_post_body_and_query_forwarded(self, service, stub_upstream):
        """POST-specific: body + query string propagated to upstream
        unchanged (covers the request payload path the per-verb test
        leaves implicit)."""
        await service.forward(
            subpath="api/dispatch", method="POST",
            headers={"content-type": "application/json"},
            query_string="trace=abc",
            body=b'{"cwd":"/repo"}',
        )
        call = stub_upstream["calls"][0]
        assert call["method"] == "POST"
        assert call["url"] == "http://engine.test:20003/data/api/dispatch"
        assert call["params"] == "trace=abc"
        assert call["content"] == b'{"cwd":"/repo"}'

    @pytest.mark.asyncio
    async def test_response_assembly_preserves_upstream_fields(
        self, service, stub_upstream,
    ):
        """End-to-end success: an arbitrary upstream response (custom
        status, body, content-type, extra header) propagates verbatim
        into the :class:`ForwardResult` — what the OCB router will hand
        FastAPI to return."""
        stub_upstream["response"] = _FakeResponse(
            201,
            {
                "content-type": "application/json; charset=utf-8",
                "x-engine-trace": "trace-7",
            },
            b'{"created":"resource-7"}',
        )
        result = await service.forward(
            subpath="api/resource", method="POST",
            headers={}, query_string="", body=b'{"name":"r"}',
        )
        assert result.status_code == 201
        assert result.content == b'{"created":"resource-7"}'
        assert result.media_type == "application/json; charset=utf-8"
        # Non-hop-by-hop upstream headers pass through unchanged.
        keys = {k.lower(): v for k, v in result.headers.items()}
        assert keys.get("x-engine-trace") == "trace-7"

    @pytest.mark.asyncio
    async def test_missing_content_type_buffers_as_plain_forward_result(
        self, service, stub_upstream,
    ):
        """A response without ``content-type`` is not SSE; it is buffered
        normally and returned with ``media_type=None``."""
        stub_upstream["response"] = _FakeResponse(204, {}, b"")

        result = await service.forward(
            subpath="api/no-content-type", method="GET",
            headers={}, query_string="", body=b"",
        )

        assert isinstance(result, ForwardResult)
        assert result.status_code == 204
        assert result.content == b""
        assert result.media_type is None

    @pytest.mark.asyncio
    async def test_caller_headers_passthrough_hop_by_hop_stripped(
        self, service, stub_upstream,
    ):
        """Caller's auth headers (including proxypass tokens) pass through
        unchanged; only hop-by-hop framing is dropped."""
        await service.forward(
            subpath="api/health", method="GET",
            headers={
                "x-service-token": "caller-token",
                "x-proxypass-token": "px-tok",
                "authorization": "Bearer abc",
                "host": "internal.localhost",  # hop-by-hop — must drop
                "content-length": "0",          # hop-by-hop — must drop
            },
            query_string="", body=b"",
        )
        sent = {
            k.lower(): v
            for k, v in stub_upstream["calls"][0]["headers"].items()
        }
        assert sent.get("x-service-token") == "caller-token"
        assert sent.get("x-proxypass-token") == "px-tok"
        assert sent.get("authorization") == "Bearer abc"
        assert "host" not in sent
        assert "content-length" not in sent

    @pytest.mark.asyncio
    async def test_upstream_connect_error_raises_engine_unreachable(
        self, service, stub_upstream,
    ):
        stub_upstream["response"] = httpx.ConnectError("refused")
        with pytest.raises(EngineUnreachable) as exc_info:
            await service.forward(
                subpath="api/health", method="GET",
                headers={}, query_string="", body=b"",
            )
        assert exc_info.value.op == "data_proxy_forward"

    @pytest.mark.asyncio
    async def test_buffer_read_error_closes_upstream_and_client(
        self, service, stub_upstream,
    ):
        class _ReadErrorResponse(_FakeResponse):
            async def aread(self):
                raise httpx.ReadError("read failed")

        response = _ReadErrorResponse(
            200, {"content-type": "application/json"}, b"",
        )
        stub_upstream["response"] = response

        with pytest.raises(EngineUnreachable) as exc_info:
            await service.forward(
                subpath="api/health", method="GET",
                headers={}, query_string="", body=b"",
            )

        assert exc_info.value.op == "data_proxy_forward"
        assert response.closed is True
        assert stub_upstream["client_closed"] is True

    @pytest.mark.asyncio
    async def test_response_headers_stripped_of_hop_by_hop(
        self, service, stub_upstream,
    ):
        stub_upstream["response"] = _FakeResponse(
            200,
            {
                "content-type": "text/plain",
                "content-length": "2",
                "transfer-encoding": "chunked",
                "connection": "keep-alive",
                "x-custom": "keep-me",
            },
            b"hi",
        )
        result = await service.forward(
            subpath="api/health", method="GET",
            headers={}, query_string="", body=b"",
        )
        keys = {k.lower() for k in result.headers}
        assert "content-length" not in keys
        assert "transfer-encoding" not in keys
        assert "connection" not in keys
        assert "x-custom" in keys

    @pytest.mark.asyncio
    async def test_read_timeout_disabled_for_streaming(
        self, service, stub_upstream,
    ):
        """The client must be built with ``read=None`` so an SSE upstream's
        long inter-event gaps don't trip a spurious unreachable error; the
        connect timeout stays bounded by ``FORWARD_TIMEOUT_SECONDS``."""
        await service.forward(
            subpath="api/eval/stream", method="POST",
            headers={}, query_string="", body=b"{}",
        )
        timeout = stub_upstream["client_kwargs"][0]["timeout"]
        assert timeout.read is None
        assert timeout.connect == dps.FORWARD_TIMEOUT_SECONDS


class TestStreaming:
    @pytest.fixture(autouse=True)
    def _tier1_pin(self, monkeypatch):
        monkeypatch.setenv(dps.ENGINE_URL_ENV, "http://engine.test:20003")
        yield

    @pytest.mark.asyncio
    async def test_event_stream_returns_streaming_result(
        self, service, stub_upstream,
    ):
        """A ``text/event-stream`` upstream is passed through as a
        :class:`StreamingForwardResult` whose body yields the upstream
        chunks in order — not buffered into a single :class:`ForwardResult`.
        Draining the body releases the upstream connection."""
        resp = _FakeResponse(
            200,
            {"content-type": "text/event-stream"},
            chunks=[b"data: turn_start\n\n", b"data: done\n\n"],
        )
        stub_upstream["response"] = resp

        result = await service.forward(
            subpath="api/eval/stream", method="POST",
            headers={}, query_string="", body=b"{}",
        )
        assert isinstance(result, StreamingForwardResult)
        assert result.status_code == 200
        assert result.media_type == "text/event-stream"

        chunks = [chunk async for chunk in result.body]
        assert chunks == [b"data: turn_start\n\n", b"data: done\n\n"]
        assert resp.closed is True
        assert stub_upstream["client_closed"] is True

    @pytest.mark.asyncio
    async def test_event_stream_with_charset_param_detected(
        self, service, stub_upstream,
    ):
        """Content-type params (``; charset=utf-8``) don't defeat SSE
        detection."""
        stub_upstream["response"] = _FakeResponse(
            200,
            {"content-type": "text/event-stream; charset=utf-8"},
            chunks=[b"data: x\n\n"],
        )
        result = await service.forward(
            subpath="api/eval/stream", method="POST",
            headers={}, query_string="", body=b"{}",
        )
        assert isinstance(result, StreamingForwardResult)
