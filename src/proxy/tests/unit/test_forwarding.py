"""Unit tests for forwarding header construction and relay bidirectional."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from sandboxproxy.community.core.forwarding import ForwardingProxy


class _FakeRequest:
    def __init__(self, headers: dict, method: str = "GET"):
        self.headers = headers
        self.method = method
        self.query_params = httpx.QueryParams({})

    async def body(self) -> bytes:
        return b""


class TestBuildHeaders:
    def test_drops_hop_by_hop(self) -> None:
        req = _FakeRequest(
            {
                "Host": "x",
                "Connection": "keep-alive",
                "Content-Length": "10",
                "X-Custom": "yes",
            }
        )
        headers = ForwardingProxy.build_headers(req)
        assert "X-Custom" in headers
        assert "Host" not in headers
        assert "Connection" not in headers

    def test_extra_headers_merged(self) -> None:
        req = _FakeRequest({"X-A": "1"})
        headers = ForwardingProxy.build_headers(
            req, extra_headers={"x-target-bot-id": "bot1"}
        )
        assert headers["x-target-bot-id"] == "bot1"


class TestForwarding:
    @pytest.mark.asyncio
    async def test_forward_streams(self) -> None:
        class RecordingTransport(httpx.AsyncBaseTransport):
            def __init__(self) -> None:
                self.requests: list[httpx.Request] = []

            async def handle_async_request(
                self, request: httpx.Request
            ) -> httpx.Response:
                self.requests.append(request)
                return httpx.Response(200, content=b"hello")

        transport = RecordingTransport()
        proxy = ForwardingProxy()
        proxy._client = httpx.AsyncClient(transport=transport)  # noqa: SLF001

        req = _FakeRequest({"X-A": "1"})
        resp = await proxy.forward(req, "http://upstream", "/echo")
        assert resp.status_code == 200
        assert transport.requests[0].url == "http://upstream/echo"
        await proxy.shutdown()

    @pytest.mark.asyncio
    async def test_start_and_shutdown(self) -> None:
        proxy = ForwardingProxy(timeout=1.0)
        await proxy.start()
        assert proxy.client is not None
        await proxy.shutdown()


class TestBidirectionalForward:
    @pytest.mark.asyncio
    async def test_teardown_on_disconnect(self) -> None:
        from sandboxproxy.community.core.relay import bidirectional_forward

        class FakeWS:
            def __init__(self, disconnect_after: int = 1):
                self.sent: list = []
                self._count = 0
                self._disconnect_after = disconnect_after

            async def receive(self):
                self._count += 1
                if self._count > self._disconnect_after:
                    return {"type": "websocket.disconnect"}
                return {"type": "websocket.receive", "text": "m"}

            async def send(self, message):
                self.sent.append(message)

        a = FakeWS()
        b = FakeWS()
        await bidirectional_forward(a, b)
        assert True
