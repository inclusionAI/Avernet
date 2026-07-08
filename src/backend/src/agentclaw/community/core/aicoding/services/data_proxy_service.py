"""Data-proxy service — forwards a request to ``{engine_base}/data/{subpath}``.

This is the business logic behind the OCB backend's
``/api/aicoding/data-proxy/*`` → engine ``/data/*`` transparent proxy.
Lives in ``core/`` (Rule 14); the FastAPI surface in
``adapters/http/aicoding/data_proxy_router.py`` is a thin shell that
translates the HTTP wire into calls on :class:`DataProxyService`.

Caller note: open-claw (oc-web) does **not** use this route for its
data plane — the frontend sends ``/data/*`` straight through proxypass
to the engine adapter (``:20003`` ``/data`` router), bypassing this
``/api/aicoding/data-proxy`` prefix. This route is the agentclaw
management-backend surface for server-side callers; it keeps the same
forward/stream contract as the engine adapter so either entry behaves
identically.

**OCB does not pick the container.** The upstream proxypass selects
the right bot's container based on the caller's identity / session /
token (e.g. ``x-proxypass-token``). OCB just appends the request to a
single configured engine base URL and forwards verbatim — purely
transparent.

Engine base URL resolution
--------------------------
:meth:`DataProxyService.resolve_engine_base` returns the engine HTTP
base URL (no trailing slash, no ``/data`` suffix; the caller appends
``/data/{subpath}``). Two tiers:

1. ``AICODING_ENGINE_URL`` env var (highest priority). Production
   deployments must set this to the proxypass entry point; tests pin
   it to an in-process fake; dev can use it to override.
2. ``SERVER_ENV in {dev, local}`` → :data:`LOCAL_DEFAULT_ENGINE_URL`
   (``http://127.0.0.1:20003``), where the local engine adapter runs.

If neither matches, :class:`EngineUrlNotConfigured` is raised — a
deployment must configure the env var.

Error model
-----------
Subclasses of :class:`DataProxyError` carry semantic state only
(``message`` / ``op``) — no HTTP status, per Rule 7 (Core Independence).
The adapter layer translates each subclass to an HTTP status.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import AsyncIterator, Mapping, Optional, Union

import httpx

log = logging.getLogger("aicoding-data-proxy")


# ── Constants ─────────────────────────────────────────────────────────────

#: Env var: deployment-supplied engine base URL. Production must point
#: this at the proxypass entry point; tests pin it to an in-process
#: fake; dev can override the localhost default.
ENGINE_URL_ENV = "AICODING_ENGINE_URL"

#: Local-mode default; kept in sync with the engine adapter's bind in
#: ``src/engine/scripts/run.sh`` (port 20003).
LOCAL_DEFAULT_ENGINE_URL = "http://127.0.0.1:20003"

#: Headers that must be dropped before re-emitting a request — they
#: describe the previous hop's framing and would corrupt the new one.
#: Everything else (including caller-supplied auth headers like
#: ``x-proxypass-token``) is forwarded as-is — OCB is a transparent
#: proxy.
_SKIP_REQUEST_HEADERS = frozenset({"host", "content-length"})

#: Hop-by-hop response framing headers; stripped so we don't mis-frame
#: a re-emitted response.
_SKIP_RESPONSE_HEADERS = frozenset({
    "content-length",
    "content-encoding",
    "transfer-encoding",
    "connection",
})

#: Forwarded request timeout. Slightly above harness-data's typical
#: per-request budget; keep below any upstream-gateway timeout (e.g.
#: nginx 60s) so we get a clean 504 from us, not from the gateway.
FORWARD_TIMEOUT_SECONDS = 30.0


# ── Errors ────────────────────────────────────────────────────────────────


class DataProxyError(Exception):
    """Base for data-proxy semantic errors.

    Subclasses carry semantic state only (``message``, ``op``) — no
    HTTP status, per Rule 7 (Core Independence). The adapter layer
    translates each subclass to an HTTPException with the right status
    code.
    """

    def __init__(self, message: str, *, op: str) -> None:
        super().__init__(message)
        self.message = message
        self.op = op


class EngineUrlNotConfigured(DataProxyError):
    """No engine base URL resolvable — deployment misconfigured.

    Raised when neither :data:`ENGINE_URL_ENV` is set nor ``SERVER_ENV``
    is one of ``dev`` / ``local``. Treated as a server-side
    configuration bug (HTTP 500), not a per-request failure.
    """


class EngineUnreachable(DataProxyError):
    """The engine could not be reached for forwarding."""


# ── Result type ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ForwardResult:
    """The upstream response, distilled down to what the router needs to
    rebuild a FastAPI ``Response``.

    ``headers`` is the response headers **after** stripping hop-by-hop
    framing (``content-length`` / ``content-encoding`` / ``transfer-
    encoding`` / ``connection``).
    """

    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""
    media_type: Optional[str] = None


@dataclass(frozen=True)
class StreamingForwardResult:
    """Upstream response whose body is streamed rather than buffered.

    Used for long-lived / unbounded responses — chiefly Server-Sent
    Events (``text/event-stream``) such as harness-data's
    ``/api/eval/stream``, forwarded verbatim through the engine adapter.
    Buffering those would block until the whole stream ends and trip the
    upstream read timeout meanwhile, surfacing as a spurious
    :class:`EngineUnreachable`.

    ``body`` is an async iterator of raw response bytes; it owns the
    ``httpx`` connection lifecycle and closes it when exhausted (or on
    error). ``headers`` already has hop-by-hop framing stripped, same as
    :class:`ForwardResult`.
    """

    status_code: int
    body: AsyncIterator[bytes]
    headers: dict[str, str] = field(default_factory=dict)
    media_type: Optional[str] = None


#: ``forward`` returns either a buffered or a streamed response.
ForwardOutcome = Union[ForwardResult, StreamingForwardResult]


# ── Helpers ───────────────────────────────────────────────────────────────


def _filter_request_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Drop hop-by-hop headers. Everything else — including caller-supplied
    auth headers such as ``x-proxypass-token`` — passes through verbatim.
    """
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in _SKIP_REQUEST_HEADERS
    }


def _filter_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Drop hop-by-hop framing headers from an upstream response."""
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in _SKIP_RESPONSE_HEADERS
    }


def _is_event_stream(media_type: Optional[str]) -> bool:
    """True when the upstream response is Server-Sent Events.

    SSE bodies are unbounded and must be streamed, not buffered. Matches
    the media type only — params like ``; charset=utf-8`` are ignored.
    """
    if not media_type:
        return False
    return media_type.split(";", 1)[0].strip().lower() == "text/event-stream"


async def _drain(
    upstream: httpx.Response, client: httpx.AsyncClient,
) -> AsyncIterator[bytes]:
    """Yield raw upstream bytes, then release the connection.

    Ties the ``httpx`` connection lifecycle to iteration: FastAPI's
    ``StreamingResponse`` consumes this to completion (or aborts on
    client disconnect), and either way the ``finally`` closes the
    response + client so the upstream connection isn't leaked.
    """
    try:
        async for chunk in upstream.aiter_raw():
            yield chunk
    finally:
        await upstream.aclose()
        await client.aclose()


# ── Service ───────────────────────────────────────────────────────────────


class DataProxyService:
    """Forwards requests to ``{engine_base}/data/{subpath}``.

    No constructor dependencies — the service is a pure transparent
    proxy. Container selection is the upstream proxypass's job, not
    OCB's.
    """

    # ── URL resolution ───────────────────────────────────────────────────

    def resolve_engine_base(self) -> str:
        """Resolve the engine HTTP base URL.

        Returns no trailing slash and no ``/data`` suffix — the caller
        appends ``/data/{subpath}``.

        Raises:
            EngineUrlNotConfigured: neither :data:`ENGINE_URL_ENV` is set
                nor ``SERVER_ENV`` is ``dev`` / ``local``.
        """
        override = (os.getenv(ENGINE_URL_ENV) or "").strip()
        if override:
            return override.rstrip("/")

        if (os.getenv("SERVER_ENV") or "").lower() in ("dev", "local"):
            return LOCAL_DEFAULT_ENGINE_URL

        raise EngineUrlNotConfigured(
            (
                f"{ENGINE_URL_ENV} env var must be set (proxypass entry "
                f"point) in production. SERVER_ENV={os.getenv('SERVER_ENV')!r} "
                f"is not a recognised dev/local fallback."
            ),
            op="data_proxy_resolve",
        )

    # ── Forwarding ───────────────────────────────────────────────────────

    async def forward(
        self,
        *,
        subpath: str,
        method: str,
        headers: Mapping[str, str],
        query_string: str,
        body: bytes,
    ) -> ForwardOutcome:
        """Resolve + forward a request to ``{engine_base}/data/{subpath}``.

        ``headers`` should be the caller's raw HTTP headers; the service
        strips hop-by-hop framing and forwards everything else verbatim
        (the caller's auth/identity headers reach the proxypass and
        engine unchanged).

        Returns a buffered :class:`ForwardResult` for ordinary responses,
        or a :class:`StreamingForwardResult` for Server-Sent Events
        (``text/event-stream``, e.g. harness-data's ``/api/eval/stream``)
        so the long-lived stream flows through live instead of being
        buffered until it ends.

        Raises:
            EngineUrlNotConfigured: from ``resolve_engine_base``.
            EngineUnreachable: ``httpx`` raised connecting to the engine.
        """
        engine_base = self.resolve_engine_base()
        upstream_url = f"{engine_base}/data/{subpath}"
        fwd_headers = _filter_request_headers(headers)

        # ``read=None`` disables the read timeout so an SSE upstream can
        # hold the connection open between events without tripping a
        # spurious timeout; the connect timeout still bounds a genuinely
        # dead engine adapter. Open as a stream so the buffer-vs-stream
        # decision can key off the *response* content-type.
        timeout = httpx.Timeout(FORWARD_TIMEOUT_SECONDS, read=None)
        client = httpx.AsyncClient(timeout=timeout)
        request = client.build_request(
            method=method,
            url=upstream_url,
            params=query_string,
            content=body,
            headers=fwd_headers,
        )
        try:
            upstream = await client.send(request, stream=True)
        except httpx.RequestError as exc:
            await client.aclose()
            log.warning(
                "[data-proxy] engine unreachable url=%s: %s",
                upstream_url, exc,
            )
            raise EngineUnreachable(
                f"engine adapter unreachable: {exc}",
                op="data_proxy_forward",
            ) from exc

        media_type = upstream.headers.get("content-type")
        resp_headers = _filter_response_headers(upstream.headers)

        # SSE → stream through; ``_drain`` owns closing the connection.
        if _is_event_stream(media_type):
            return StreamingForwardResult(
                status_code=upstream.status_code,
                body=_drain(upstream, client),
                headers=resp_headers,
                media_type=media_type,
            )

        # Everything else → buffer fully, then release the connection.
        try:
            content = await upstream.aread()
        except httpx.RequestError as exc:
            await upstream.aclose()
            await client.aclose()
            log.warning(
                "[data-proxy] engine unreachable url=%s: %s",
                upstream_url, exc,
            )
            raise EngineUnreachable(
                f"engine adapter unreachable: {exc}",
                op="data_proxy_forward",
            ) from exc
        await upstream.aclose()
        await client.aclose()

        return ForwardResult(
            status_code=upstream.status_code,
            headers=resp_headers,
            content=content,
            media_type=media_type,
        )
