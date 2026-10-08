"""HTTP reverse-proxy forwarding to a resolved upstream (transport-agnostic core)."""

from __future__ import annotations

from typing import Any

import httpx

from sandboxproxy.community.logger import get_logger

logger = get_logger("forwarding")


class ForwardingProxy:
    """Forward an incoming request to a resolved upstream over HTTP.

    Returns the raw ``httpx.Response`` (streamed); the delivery adapter is
    responsible for translating it into an ASGI/WSGI response — keeping this
    core component transport-agnostic.
    """

    def __init__(self, *, timeout: float = 86400.0) -> None:
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True
            )

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        assert self._client is not None, "ForwardingProxy has not started"
        return self._client

    @staticmethod
    def build_headers(
        request: Any,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        dropped = {"host", "connection", "content-length"}
        headers = {k: v for k, v in request.headers.items() if k.lower() not in dropped}
        if extra_headers:
            headers.update(extra_headers)
        return headers

    async def forward(
        self,
        request: Any,
        upstream_url: str,
        target_path: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        url = upstream_url.rstrip("/") + target_path
        headers = self.build_headers(request, extra_headers=extra_headers)

        body = await request.body()
        upstream_req = self.client.build_request(
            method=request.method,
            url=url,
            headers=headers,
            content=body or None,
            params=request.query_params.multi_items(),
        )
        return await self.client.send(upstream_req, stream=True)
