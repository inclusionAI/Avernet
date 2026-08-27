"""BaaS relay client — HTTP client for the upstream BaaS relay-sessions API."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import httpx

from sandboxproxy.community.logger import get_logger

logger = get_logger("relay_client")

_STATUS_ACTIVE = "active"
_STATUS_CLOSED = "closed"


class BaasRelayClient:
    """Client for ``GET/PUT /api/v1/paas/relay-sessions/{session_id}``.

    Writes the enterprise route contract::

        {
            "connected_server_instance": <ip>,
            "connected_route_info": {"worker_pid": <pid>, "socket_path": <uds>},
            "status": "active" | "closed",
        }
    """

    def __init__(
        self,
        baas_host: str,
        *,
        route_info: dict[str, Any] | None = None,
        instance: str = "",
        worker_pid: int = 0,
        socket_path: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._baas_host = baas_host.rstrip("/")
        self._route_info = route_info or {}
        self._instance = instance
        self._worker_pid = worker_pid
        self._socket_path = socket_path
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def _base_url(self) -> str:
        return f"{self._baas_host}/api/v1/paas/relay-sessions"

    def _active_route_info(self) -> dict[str, Any]:
        if self._route_info:
            return dict(self._route_info)
        return {
            "worker_pid": self._worker_pid,
            "socket_path": self._socket_path,
        }

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(
        self, method: str, session_id: str, *, json: dict[str, Any] | None = None
    ) -> httpx.Response | None:
        if self._client is None:
            await self.start()
        assert self._client is not None
        url = f"{self._base_url}/{session_id}"
        try:
            return await self._client.request(method, url, json=json)
        except httpx.HTTPError as exc:  # pragma: no cover - retried by caller
            logger.warning("relay-sessions %s %s failed: %s", method, url, exc)
            return None

    async def upsert_route_active(self, session_id: str) -> bool:
        payload = {
            "status": _STATUS_ACTIVE,
            "connected_server_instance": self._instance,
            "connected_route_info": self._active_route_info(),
        }
        resp = await self._request("PUT", session_id, json=payload)
        return bool(resp is not None and resp.status_code < 400)

    async def get_route_info(self, session_id: str) -> dict[str, Any] | None:
        resp = await self._request("GET", session_id)
        if resp is None:
            return None
        if resp.status_code >= 400:
            return None
        return cast(dict[str, Any], resp.json())

    async def mark_route_closed(self, session_id: str) -> bool:
        for attempt in range(3):
            resp = await self._request(
                "PUT", session_id, json={"status": _STATUS_CLOSED}
            )
            if resp is not None:
                if resp.status_code < 400:
                    return True
                if resp.status_code == 404:
                    return True  # already deleted — treat as success
            if attempt < 2:
                await asyncio.sleep(2**attempt)  # 1s, 2s
        return False
