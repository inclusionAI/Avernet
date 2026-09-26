"""DataProxyService Protocol — engine ``/data/*`` reverse proxy.

The engine adapter exposes ``/data/*`` to forward to whatever upstream
data plane that engine implements (aicoding forwards to harness-data on
127.0.0.1:8765; other engines may forward elsewhere or simply not
declare the capability).

The HTTP router (``api/aicoding/data_proxy_router.py``) is intentionally
thin:

1. Check :data:`engine.community.core.engine.capability.Capability.DATA_PROXY_FORWARD`
   on the active engine. Engines that don't declare it 501 here, the
   route never touches plugin logic.
2. Pull :class:`DataProxyService` off ``EngineManager.get_instance()``.
3. Delegate to :meth:`DataProxyService.forward` and render its result.

The forwarding contract (URL shape, header filtering, upstream choice)
is each plugin's concern — the protocol only fixes the call signature
and the typed errors.

Error model
-----------
:class:`DataProxyError` subclasses carry **semantic** state only
(``message``); HTTP status mapping lives at the adapter layer (the
router). :class:`UpstreamUnreachable` is the canonical "the engine
couldn't reach its data plane" error; plugins may add their own
subclasses (e.g. an auth-failure variant) and the router will fall
back to 500 for anything it hasn't explicitly mapped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Mapping, Optional, Protocol, Union, runtime_checkable


# ── Errors ────────────────────────────────────────────────────────────────


class DataProxyError(Exception):
    """Base for data-proxy semantic errors.

    Carries semantic state only — no HTTP status (Rule 7 / Core
    Independence). The HTTP adapter maps each subclass to a status.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UpstreamUnreachable(DataProxyError):
    """The plugin could not reach its configured upstream."""


# ── Result type ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ForwardResult:
    """Upstream response distilled to what the router needs to rebuild a
    FastAPI ``Response``.

    ``headers`` should already have hop-by-hop framing stripped
    (``content-length`` / ``content-encoding`` / ``transfer-encoding`` /
    ``connection``) — they describe the previous hop and would mis-frame
    the new response.
    """

    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""
    media_type: Optional[str] = None


@dataclass(frozen=True)
class StreamingForwardResult:
    """Upstream response whose body is streamed chunk-by-chunk rather than
    buffered.

    Used for long-lived / unbounded responses — chiefly Server-Sent
    Events (``text/event-stream``) like harness-data's
    ``/api/eval/stream``, where the upstream holds the connection open
    and trickles events turn-by-turn. Buffering those (the
    :class:`ForwardResult` path) would block until the whole stream ends
    and trip the upstream read timeout in the meantime — surfacing as a
    spurious :class:`UpstreamUnreachable`.

    ``body`` is an async iterator of raw response bytes. The plugin owns
    the upstream connection's lifecycle: the iterator MUST close the
    underlying ``httpx`` response/client when exhausted (or on error) so
    the connection isn't leaked. ``headers`` already has hop-by-hop
    framing stripped, same as :class:`ForwardResult`.
    """

    status_code: int
    body: AsyncIterator[bytes]
    headers: dict[str, str] = field(default_factory=dict)
    media_type: Optional[str] = None


#: A plugin's ``forward`` returns either a buffered or a streamed response.
ForwardOutcome = Union[ForwardResult, StreamingForwardResult]


# ── Protocol ──────────────────────────────────────────────────────────────


@runtime_checkable
class DataProxyService(Protocol):
    """Forward ``/data/<path>`` requests to an engine-defined upstream."""

    async def forward(
        self,
        *,
        path: str,
        method: str,
        headers: Mapping[str, str],
        query_string: str,
        body: bytes,
    ) -> ForwardOutcome:
        """Forward a request and return the upstream response.

        Returns either a buffered :class:`ForwardResult` or, for
        long-lived streaming upstreams (SSE), a
        :class:`StreamingForwardResult` the router renders as a
        ``StreamingResponse``.

        Raises:
            UpstreamUnreachable: the configured upstream couldn't be
                reached. Plugins may raise other :class:`DataProxyError`
                subclasses for semantic failures (auth, etc.); the
                router falls back to 500 for unmapped subclasses.
        """
        ...


__all__ = [
    "DataProxyError",
    "DataProxyService",
    "ForwardOutcome",
    "ForwardResult",
    "StreamingForwardResult",
    "UpstreamUnreachable",
]
