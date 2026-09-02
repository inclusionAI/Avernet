"""BaaS-backed community engine adapter transport tests."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agentclaw.community.core.service_bot.services.baas_service import (
    BaasServiceError,
    HttpConnectionInfo,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTimeoutError,
)
from agentclaw.community.plugins.community.device_adapter_transport import (
    CommunityDeviceAdapterTransport,
)


def _response(status: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("POST", "https://proxy.example/api/sessions"),
    )


def _transport(response: httpx.Response | None = None):
    baas = MagicMock()
    if response is not None:
        baas.invoke_http.return_value = response
    return CommunityDeviceAdapterTransport(baas), baas


def test_invoke_routes_through_baas_with_binding_context():
    transport, baas = _transport(
        _response(200, {"success": True, "data": {"id": "s-1"}})
    )

    result = asyncio.run(
        transport.invoke(
            conn_info={
                "binding_id": 17,
                "engine_port": 20003,
                "tenant": "tenant-a",
                "device_affinity": "user-1",
                "device_uuid": "device-1",
            },
            method="POST",
            path="/api/sessions",
            body={"title": "test"},
            params={"stage": "draft"},
            timeout=10.0,
        )
    )

    assert result == {"success": True, "data": {"id": "s-1"}}
    baas.invoke_http.assert_called_once_with(
        bind_id=17,
        port=20003,
        path="/api/sessions",
        method="POST",
        json={"title": "test"},
        params={"stage": "draft"},
        tenant="tenant-a",
        device_affinity="user-1",
        device_uuid="device-1",
        auth_header="x-proxypass-token",
        timeout=10.0,
    )


def test_invoke_accepts_legacy_bind_id_alias():
    transport, baas = _transport(_response(200, {"success": True}))

    asyncio.run(
        transport.invoke(
            conn_info={"bind_id": 23}, method="GET", path="/api/engine/status"
        )
    )

    assert baas.invoke_http.call_args.kwargs["bind_id"] == 23


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (404, DeviceAdapterEndpointNotFoundError),
        (503, DeviceAdapterHTTPStatusError),
    ],
)
def test_invoke_maps_http_statuses(status, expected):
    transport, _ = _transport(_response(status, {"detail": "failed"}))

    with pytest.raises(expected):
        asyncio.run(
            transport.invoke(
                conn_info={"binding_id": 1}, method="GET", path="/api/test"
            )
        )


def test_invoke_maps_explicit_timeout():
    transport, baas = _transport()
    baas.invoke_http.side_effect = httpx.ReadTimeout(
        "slow", request=httpx.Request("GET", "https://proxy.example/api/test")
    )

    with pytest.raises(DeviceAdapterTimeoutError):
        asyncio.run(
            transport.invoke(
                conn_info={"binding_id": 1},
                method="GET",
                path="/api/test",
                timeout=1.0,
            )
        )


def test_invoke_maps_baas_resolution_error():
    transport, baas = _transport()
    baas.invoke_http.side_effect = BaasServiceError("no active device")

    with pytest.raises(ValueError, match="resolve adapter connection"):
        asyncio.run(
            transport.invoke(
                conn_info={"binding_id": 1}, method="GET", path="/api/test"
            )
        )


def test_stream_uses_baas_http_info_and_closes():
    transport, baas = _transport()
    baas.get_http_info.return_value = HttpConnectionInfo(
        http_url="https://proxy.example/api/chat/stream",
        token="token-1",
        target="ARCA_device@0:20003",
    )
    response = MagicMock()
    response.status_code = 200
    response.headers = {"content-type": "text/event-stream"}
    response.aclose = AsyncMock()

    async def chunks():
        yield b"one"
        yield b"two"

    response.aiter_bytes.side_effect = chunks
    client = MagicMock()
    client.build_request.return_value = httpx.Request(
        "POST", "https://proxy.example/api/chat/stream"
    )
    client.send = AsyncMock(return_value=response)
    client.aclose = AsyncMock()

    async def exercise_stream():
        with patch("httpx.AsyncClient", return_value=client):
            stream = await transport.stream(
                conn_info={"binding_id": 9, "engine_port": 20003},
                method="POST",
                path="/api/chat/stream",
                body={"message": "hello"},
            )
            body = [chunk async for chunk in stream.body]
            await stream.close()
            return stream, body

    stream, body = asyncio.run(exercise_stream())

    assert stream.status_code == 200
    assert body == [b"one", b"two"]
    baas.get_http_info.assert_called_once_with(
        bind_id=9,
        port=20003,
        path="/api/chat/stream",
        tenant=None,
        device_affinity=None,
        device_uuid=None,
    )
    client.build_request.assert_called_once_with(
        method="POST",
        url="https://proxy.example/api/chat/stream",
        json={"message": "hello"},
        params=None,
        headers={"x-proxypass-token": "token-1"},
    )
    response.aclose.assert_awaited_once()
    client.aclose.assert_awaited_once()
