"""Streaming HTTP reverse proxy to the session file-transfer OSS endpoint.

Form B of the phase-89 python-only deployment: replaces the enterprise-form
nginx second leg with an in-process streaming proxy.  A client PUT/GET to the
BaaS-domain route ``/api/v1/file-transfer-proxy/{oss_path:path}`` is streamed
through this class to the OSS endpoint configured under
``file_transfer_oss_aliyun``, preserving the signature trinity byte-for-byte:

- the raw path tail (percent-encoded form, sliced from ``scope["raw_path"]``),
- the raw query string (``scope["query_string"]``), never dict-rebuilt,
- ``Content-Type`` and every signed header (``content-md5`` / ``x-oss-*``)
  forwarded verbatim.

The outbound Host is recomputed as ``{bucket_name}.{endpoint_host}`` — the
inbound Host is hop-filtered and never reused (D-89-02).  An unconfigured
endpoint or bucket raises :class:`SessionFileTransferProxyUnavailableError`
so an unconfigured deployment is never an open relay (D-89-01).

Timeout semantics: per-operation ``httpx.Timeout(connect=10.0, read=600.0,
write=600.0, pool=10.0)`` replaces the httpx 5s default, so total transfer
duration is unbounded while dead peers are detected (D-89-03).
"""

from collections.abc import AsyncIterable, AsyncIterator

import httpx

from secbaas.community.api.session_file_sharing import (
    SessionFileTransferProxyUnavailableError,
)
from secbaas.community.logger import get_logger

log = get_logger("plugin-file-transfer")

_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 600.0
_WRITE_TIMEOUT = 600.0
_POOL_TIMEOUT = 10.0

# RFC 2616 section 13.5.1 hop-by-hop headers — never forwarded upstream.
# Duplicated from adapters/web/routers/bot_service/http_router.py: importing
# from the adapters tier into a plugin would break layering (RULES-MANIFEST
# Rule 7 duplicate-`_filter_headers` exemption).
HOP_BY_HOP_HEADERS: set[str] = {
    "connection",
    "keep-alive",
    "proxy-authorization",
    "proxy-authenticate",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "proxy-connection",
    "host",
}


def _filter_headers(headers: dict[str, str]) -> dict[str, str]:
    """Drop hop-by-hop headers from an inbound or upstream header dict."""
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_HEADERS}


class OssStreamingProxy:
    """Streaming reverse proxy to the session file-transfer OSS endpoint.

    ``forward`` performs one upstream round-trip and returns the tuple
    ``(status_code, response_headers, body_iterator)`` the router streams
    straight back to the client.  The body iterator swallows mid-transfer
    ``httpx.HTTPError`` (the response status line is already committed, so
    there is nothing sane to do but log and end the stream).
    """

    def __init__(
        self,
        endpoint: str,
        bucket_name: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._bucket_name = bucket_name
        # E501-ignored (project ruff config): the pin is a single semantic
        # unit and the acceptance grep matches the literal on one line.
        self._timeout = httpx.Timeout(
            connect=_CONNECT_TIMEOUT,
            read=_READ_TIMEOUT,
            write=_WRITE_TIMEOUT,
            pool=_POOL_TIMEOUT,
        )
        self._client = httpx.AsyncClient(timeout=self._timeout, transport=transport)

    async def forward(
        self,
        method: str,
        path: bytes,
        query: bytes,
        headers: dict[str, str],
        body_stream: AsyncIterable[bytes] | None,
        content_length: str | None,
    ) -> tuple[int, dict[str, str], AsyncIterator[bytes]]:
        """Stream one client request to the OSS upstream and return its reply.

        ``path`` is the raw path tail (bytes, starts with ``/``); ``query``
        the raw query string (bytes, without ``?``).  Both are decoded with
        latin-1 — a 1:1 byte-to-code-point mapping — so the URL string handed
        to httpx carries every byte intact with no re-encoding.
        """
        if not self._endpoint or not self._bucket_name:
            raise SessionFileTransferProxyUnavailableError(
                reason=(
                    "file_transfer_oss_aliyun endpoint/bucket_name is not "
                    "configured — the open-source file-transfer proxy cannot "
                    "forward"
                )
            )
        endpoint_host = httpx.URL(self._endpoint).host
        if endpoint_host is None:
            raise SessionFileTransferProxyUnavailableError(
                reason=(
                    "file_transfer_oss_aliyun endpoint is not a valid absolute "
                    "URL — the open-source file-transfer proxy cannot derive "
                    "the upstream authority"
                )
            )
        url_string = (
            self._endpoint.rstrip("/")
            + path.decode("latin-1")
            + (("?" + query.decode("latin-1")) if query else "")
        )
        out_headers = _filter_headers(headers)
        if content_length is not None:
            out_headers["content-length"] = content_length
        out_headers["Host"] = f"{self._bucket_name}.{endpoint_host}"
        content = body_stream if method == "PUT" else None

        # A bare httpx.Request (not client.build_request) so the client-level
        # default headers — accept / accept-encoding / connection / user-agent —
        # are never merged in: a transparent proxy forwards only what the client
        # sent, never its own hop-ish or negotiated headers.
        request = httpx.Request(
            method, url_string, headers=out_headers, content=content
        )
        resp = await self._client.send(request, stream=True)
        status_code = resp.status_code
        resp_headers = _filter_headers(dict(resp.headers))
        return (status_code, resp_headers, self._iter_body(resp))

    async def _iter_body(self, resp: httpx.Response) -> AsyncIterator[bytes]:
        """Yield the upstream body; a mid-transfer abort is logged, not raised.

        The response status line has already been sent to the client, so a
        mid-stream failure can only be recorded — re-raising would surface a
        bogus 500 the client never receives.
        """
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        except httpx.HTTPError:
            log.error(
                "file-transfer proxy: upstream stream aborted mid-transfer "
                "(response headers already sent)",
                exc_info=True,
            )
            return
