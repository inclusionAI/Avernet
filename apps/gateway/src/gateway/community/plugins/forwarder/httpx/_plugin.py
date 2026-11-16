"""Bare forwarder plugin — httpx-backed streaming reverse proxy.

Streams the response body as raw bytes (``aiter_raw``) so the gateway is a
transparent proxy: it re-emits exactly what the upstream sent, including
content-encoding, without buffering the whole body. Hop-by-hop headers are
dropped in both directions.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx

from gateway.community.logger import get_logger
from gateway.community.spi.forwarder import (
    Forwarder,
    ForwardRequest,
    ForwardResponse,
    strip_hop_by_hop,
    strip_hop_by_hop_items,
)

logger = get_logger("http-forwarder")


class HttpxForwarder(Forwarder):
    """An httpx-backed :class:`Forwarder`.

    A single ``AsyncClient`` is reused across requests (connection pooling). If
    none is injected one is created lazily and owned by this instance; call
    :meth:`aclose` on shutdown. Tests inject a client wired to a mock transport.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 120.0,
        write_timeout: float = 5.0,
        pool_timeout: float = 5.0,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._write_timeout = write_timeout
        self._pool_timeout = pool_timeout

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=self._connect_timeout,
                    read=self._read_timeout,
                    write=self._write_timeout,
                    pool=self._pool_timeout,
                ),
            )
        return self._client

    @asynccontextmanager
    async def forward(
        self, request: ForwardRequest
    ) -> AsyncGenerator[ForwardResponse, None]:
        client = self._get_client()
        declared_content_length = next(
            (
                value
                for name, value in request.headers.items()
                if name.lower() == "content-length"
            ),
            "unknown",
        )
        logger.info(
            "forwarding request %s %s headers_len=%d declared_content_length=%s",
            request.method,
            request.url,
            len(request.headers),
            declared_content_length,
        )
        try:
            upstream = client.build_request(
                request.method,
                request.url,
                headers=strip_hop_by_hop(request.headers),
                content=request.body,
            )
            # A request body is a one-shot stream. Never inherit client auth or
            # redirect policy: challenges and 307/308 both require replay.
            response = await client.send(
                upstream,
                stream=True,
                auth=None,
                follow_redirects=False,
            )
        except Exception:
            logger.exception(
                "upstream send failed for %s %s", request.method, request.url
            )
            raise
        finally:
            if request.body is not None:
                await request.body.aclose()
        logger.info(
            "upstream response status=%d headers=%s",
            response.status_code,
            response.headers.multi_items(),
        )
        try:
            # multi_items() preserves duplicate response headers (Set-Cookie).
            yield ForwardResponse(
                status_code=response.status_code,
                headers=strip_hop_by_hop_items(response.headers.multi_items()),
                body=response.aiter_raw(),
            )
        finally:
            await response.aclose()

    async def aclose(self) -> None:
        """Close the owned client (no-op for an injected one)."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
