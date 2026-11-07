"""Reusable async HTTP/1.1 transport for online model calls."""

import ssl
from typing import Any

import aiohttp


class ModelHTTPError(RuntimeError):
    """Expose only the status to degradation/observability, never response text."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"Model endpoint returned HTTP {status_code}")


class AsyncModelHttpClient:
    """Host-owned session; no retries or application admission limits.

    HTTP/1.1 cancellation closes the affected socket without cancelling peers.
    Idle sockets expire after aiohttp's default keepalive interval. Constructing
    providers before an event loop starts is supported; first use opens session.
    """

    def __init__(self, timeout: float):
        self._timeout = timeout
        self._session: aiohttp.ClientSession | None = None
        self._closed = False
        # Build trust configuration once, outside the request path. Honors the
        # platform trust store and SSL_CERT_FILE/SSL_CERT_DIR.
        self._ssl = ssl.create_default_context()

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def post_json(
        self, url: str, *, headers: dict[str, str], json: dict
    ) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("Model HTTP client is closed")
        if self._session is None:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    limit=0, limit_per_host=0, ssl=self._ssl
                ),
                timeout=aiohttp.ClientTimeout(total=self._timeout),
                trust_env=True,
                cookie_jar=aiohttp.DummyCookieJar(),
            )
        async with self._session.post(
            url, headers=headers, json=json, allow_redirects=False
        ) as response:
            # Consume error bodies too so 401/429 connections can be reused.
            # Never retain or log their contents in exceptions.
            await response.read()
            if response.status >= 300:
                raise ModelHTTPError(response.status)
            payload = await response.json(content_type=None)
            if not isinstance(payload, dict):
                raise TypeError("Model response must be a JSON object")
            return payload

    async def aclose(self) -> None:
        self._closed = True
        if self._session is not None:
            await self._session.close()
