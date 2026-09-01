"""Community ``DeviceAdapterTransport`` backed by BaaS connection discovery.

The community profile owns no container runtime. BaaS does, and its ``/http-info``
contract hides whether a device is backed by ARCA, ACK, Docker, or another
platform. This transport routes by BaaS binding id and never reads
runtime-specific target prefixes or ARCA configuration.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Optional

import httpx
from injector import inject

from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BaasServiceError,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterStreamResponse,
    DeviceAdapterTimeoutError,
    DeviceAdapterTransport,
)

logger = get_logger()

_DEFAULT_TIMEOUT = 120.0


def _binding_id(conn_info: dict[str, Any]) -> int:
    raw = conn_info.get("binding_id", conn_info.get("bind_id"))
    if isinstance(raw, bool):
        raise ValueError("BaaS adapter connection requires a valid binding_id")
    try:
        binding_id = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("BaaS adapter connection requires a valid binding_id") from exc
    if binding_id <= 0:
        raise ValueError("BaaS adapter connection requires a valid binding_id")
    return binding_id


class CommunityDeviceAdapterTransport(DeviceAdapterTransport):
    """Forward engine-adapter requests through BaaS ``/http-info``."""

    @inject
    def __init__(self, baas_service: BaasService) -> None:
        self._baas_service = baas_service

    @staticmethod
    def _invoke_kwargs(
        conn_info: dict[str, Any],
        method: str,
        path: str,
        body: Optional[dict[str, Any]],
        params: Optional[dict[str, Any]],
        timeout: float | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "bind_id": _binding_id(conn_info),
            "port": int(conn_info.get("engine_port", 20003)),
            "path": path,
            "method": method,
            "json": body,
            "params": params,
            "tenant": conn_info.get("tenant") or None,
            "device_affinity": conn_info.get("device_affinity") or None,
            "device_uuid": conn_info.get("device_uuid") or None,
            "auth_header": "x-proxypass-token",
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        return kwargs

    @staticmethod
    def _map_status_error(exc: httpx.HTTPStatusError) -> Exception:
        status_code = exc.response.status_code
        if status_code == 404:
            return DeviceAdapterEndpointNotFoundError(
                f"Adapter returned HTTP 404: {exc.response.text}"
            )
        return DeviceAdapterHTTPStatusError(status_code, exc.response.text)

    async def invoke(
        self,
        conn_info: dict[str, Any],
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        try:
            response = await asyncio.to_thread(
                self._baas_service.invoke_http,
                **self._invoke_kwargs(conn_info, method, path, body, params, timeout),
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Adapter returned a non-object JSON response")
            return result
        except httpx.HTTPStatusError as exc:
            raise self._map_status_error(exc) from exc
        except httpx.RequestError as exc:
            if timeout is not None and isinstance(exc, httpx.TimeoutException):
                raise DeviceAdapterTimeoutError(
                    f"Adapter request timed out: {method} {path}"
                ) from exc
            raise ValueError(f"Failed to connect to adapter: {exc}") from exc
        except BaasServiceError as exc:
            raise ValueError(f"Failed to resolve adapter connection: {exc}") from exc

    async def stream(
        self,
        conn_info: dict[str, Any],
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        *,
        timeout: float | None = None,
    ) -> DeviceAdapterStreamResponse:
        request_timeout = timeout if timeout is not None else _DEFAULT_TIMEOUT
        try:
            info = await asyncio.to_thread(
                self._baas_service.get_http_info,
                bind_id=_binding_id(conn_info),
                port=int(conn_info.get("engine_port", 20003)),
                path=path,
                tenant=conn_info.get("tenant") or None,
                device_affinity=conn_info.get("device_affinity") or None,
                device_uuid=conn_info.get("device_uuid") or None,
            )
            client = httpx.AsyncClient(timeout=request_timeout)
            try:
                request = client.build_request(
                    method=method,
                    url=info.http_url,
                    json=body,
                    params=params,
                    headers={"x-proxypass-token": info.token},
                )
                response = await client.send(request, stream=True)
            except Exception:
                await client.aclose()
                raise
        except httpx.RequestError as exc:
            if timeout is not None and isinstance(exc, httpx.TimeoutException):
                raise DeviceAdapterTimeoutError(
                    f"Adapter stream timed out: {method} {path}"
                ) from exc
            raise ValueError(f"Failed to open adapter stream: {exc}") from exc
        except BaasServiceError as exc:
            raise ValueError(f"Failed to resolve adapter connection: {exc}") from exc

        closed = False

        async def close() -> None:
            nonlocal closed
            if closed:
                return
            closed = True
            try:
                await response.aclose()
            finally:
                await client.aclose()

        async def stream_body() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await close()

        logger.info(
            "[CommunityDeviceAdapterTransport] stream opened: %s %s status=%s",
            method,
            path,
            response.status_code,
        )
        return DeviceAdapterStreamResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=stream_body(),
            close=close,
        )
