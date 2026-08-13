"""Framework-neutral request/response models for the Forwarder SPI.

Forwarder Plugin API contract version: **2**. Version 1 represented an inbound
body as fully-buffered ``ForwardRequest.content: bytes``. Version 2 replaces that
field with the closeable, one-shot ``ForwardRequest.body`` stream so large
uploads can propagate backpressure instead of consuming memory proportional to
the complete payload. The Python class names remain stable; the contract version
describes their semantics rather than creating parallel ``V1``/``V2`` types.

The delivery adapter translates request metadata and a one-shot body stream into
a :class:`ForwardRequest`, then streams a :class:`ForwardResponse` back. Neither
type depends on a web framework, so transport implementations remain replaceable.

Response headers are kept as an ordered list of ``(name, value)`` pairs rather
than a dict so **repeatable** headers (``Set-Cookie``, ``Vary``, ``Link``, …)
survive verbatim — folding them into a dict would merge or destroy them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

# Hop-by-hop headers must not be forwarded end to end (RFC 7230 §6.1).
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)


def _connection_named(items: Iterable[tuple[str, str]]) -> set[str]:
    """Header names listed in any ``Connection`` header value (RFC 7230 §6.1).

    Such headers are connection-scoped and must also be dropped.
    """
    named: set[str] = set()
    for key, value in items:
        if key.lower() == "connection":
            named |= {tok.strip().lower() for tok in value.split(",") if tok.strip()}
    return named


def strip_hop_by_hop(headers: Mapping[str, str]) -> dict[str, str]:
    """Return *headers* without hop-by-hop entries (for single-valued headers)."""
    banned = HOP_BY_HOP_HEADERS | _connection_named(headers.items())
    return {k: v for k, v in headers.items() if k.lower() not in banned}


def strip_hop_by_hop_items(
    items: Iterable[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Drop hop-by-hop pairs while preserving order and duplicate names."""
    pairs = list(items)
    banned = HOP_BY_HOP_HEADERS | _connection_named(pairs)
    return [(k, v) for k, v in pairs if k.lower() not in banned]


class ForwardRequestBody(Protocol):
    """Closeable one-shot request bytes accepted by a :class:`Forwarder`.

    ``aclose`` must be safe to call after full consumption and more than once.
    """

    def __aiter__(self) -> AsyncIterator[bytes]: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class ForwardRequest:
    """A request to forward to an upstream, addressed by absolute URL.

    ``body`` is either absent or a closeable, one-shot asynchronous byte stream.
    It intentionally cannot be supplied as buffered ``content``: buffering would
    defeat backpressure and make Gateway memory usage grow with upload size.
    Once a forwarder's context manager is entered, that forwarder owns the body
    and must close it after sending finishes, fails, or is cancelled.
    """

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: ForwardRequestBody | None = None


@dataclass
class ForwardResponse:
    """An upstream response; body streamed as raw byte chunks.

    ``headers`` is an ordered ``(name, value)`` list so duplicate headers
    (notably ``Set-Cookie``) are preserved exactly as the upstream sent them.
    """

    status_code: int
    headers: list[tuple[str, str]]
    body: AsyncIterator[bytes]
