"""Bare forwarder plugin — httpx-backed streaming reverse proxy.

Streams the response body as raw bytes (``aiter_raw``) so the gateway is a
transparent proxy: it re-emits exactly what the upstream sent, including
content-encoding, without buffering the whole body. Hop-by-hop headers are
dropped in both directions.

Idempotent requests (GET, HEAD, OPTIONS) are retried up to twice with
exponential backoff on transient transport errors (timeout, connection reset,
protocol error). Requests with a body are never retried — the body is a one-shot
stream that cannot be replayed.
"""

from __future__ import annotations

import asyncio
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

# ── retry configuration ──────────────────────────────────────────────────

#: Transient transport errors that are safe to retry for idempotent requests.
#: ``httpx.TimeoutException`` is the base for ``ConnectTimeout``, ``ReadTimeout``,
#: ``WriteTimeout``, and ``PoolTimeout`` — all four are retryable.
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)

#: HTTP methods whose requests are safe to retry — they carry no body and are
#: idempotent by the HTTP specification. A request with a body is **never**
#: retried regardless of method, because the body stream cannot be replayed.
_IDEMPOTENT_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

#: Maximum number of retry attempts (2 retries = 3 total attempts).
_MAX_RETRIES: int = 2

#: Base backoff in seconds; the *n*-th retry waits ``base * 2^n`` seconds.
_BACKOFF_BASE: float = 0.5


async def _send_with_retry(
    client: httpx.AsyncClient,
    request: httpx.Request,
    *,
    max_retries: int = _MAX_RETRIES,
    backoff_base: float = _BACKOFF_BASE,
) -> httpx.Response:
    """Send *request*, retrying on transient errors for idempotent methods.

    Retries only when the HTTP method is idempotent (GET, HEAD, OPTIONS) and
    the error is a transient transport failure. Non-idempotent methods and
    non-retryable errors propagate immediately.
    """
    can_retry = request.method.upper() in _IDEMPOTENT_METHODS
    for attempt in range(max_retries + 1):
        try:
            return await client.send(
                request,
                stream=True,
                auth=None,
                follow_redirects=False,
            )
        except _RETRYABLE_EXCEPTIONS as exc:
            if not can_retry or attempt >= max_retries:
                raise
            delay = backoff_base * (2**attempt)
            logger.warning(
                "upstream send attempt %d/%d failed for %s %s: %s; retrying in %.1fs",
                attempt + 1,
                max_retries + 1,
                request.method,
                request.url,
                exc,
                delay,
            )
            await asyncio.sleep(delay)


class HttpxForwarder(Forwarder):
    """An httpx-backed :class:`Forwarder`.

    A single ``AsyncClient`` is reused across requests (connection pooling). If
    none is injected one is created lazily and owned by this instance; call
    :meth:`aclose` on shutdown. Tests inject a client wired to a mock transport.

    Idempotent GET, HEAD, and OPTIONS requests are automatically retried (up to
    2 retries, exponential backoff) on transient transport errors such as
    timeouts, connection resets, and protocol errors.
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=120.0, write=5.0, pool=5.0),
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
            # Idempotent GET/HEAD/OPTIONS are retried on transient errors.
            response = await _send_with_retry(client, upstream)
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
