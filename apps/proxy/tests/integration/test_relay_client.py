"""Integration tests — relay-sessions lifecycle against a mocked BaaS API."""

from __future__ import annotations

import httpx
import pytest

from sandboxproxy.community.plugins.relay_client.baas import BaasRelayClient


class _RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


@pytest.mark.integration
class TestBaasRelayClientLifecycle:
    @pytest.mark.asyncio
    async def test_active_then_closed(self) -> None:
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json_body(request))
            return httpx.Response(200, json={"ok": True})

        transport = _RecordingTransport(handler)
        client = BaasRelayClient(
            "http://baas.test", instance="127.0.0.1", worker_pid=4242
        )
        client._client = httpx.AsyncClient(transport=transport)  # noqa: SLF001

        ok = await client.upsert_route_active("sess-1")
        assert ok is True
        assert seen == [
            {
                "status": "active",
                "connected_server_instance": "127.0.0.1",
                "connected_route_info": {"worker_pid": 4242, "socket_path": None},
            }
        ]

        assert transport.requests[0].method == "PUT"
        assert transport.requests[0].url.path.endswith(
            "/api/v1/paas/relay-sessions/sess-1"
        )

        await client.mark_route_closed("sess-1")
        assert seen[-1]["status"] == "closed"
        await client.shutdown()

    @pytest.mark.asyncio
    async def test_upsert_failure_returns_false(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = BaasRelayClient("http://baas.test", instance="127.0.0.1")
        client._client = httpx.AsyncClient(  # noqa: SLF001
            transport=_RecordingTransport(handler)
        )
        ok = await client.upsert_route_active("sess-1")
        assert ok is False
        await client.shutdown()

    @pytest.mark.asyncio
    async def test_get_route_info(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"session_id": "sess-1", "status": "active"}
            )

        client = BaasRelayClient("http://baas.test", instance="127.0.0.1")
        client._client = httpx.AsyncClient(  # noqa: SLF001
            transport=_RecordingTransport(handler)
        )
        info = await client.get_route_info("sess-1")
        assert info == {"session_id": "sess-1", "status": "active"}
        await client.shutdown()


def json_body(request: httpx.Request) -> dict:
    import json as _json

    return _json.loads(request.content.decode())
