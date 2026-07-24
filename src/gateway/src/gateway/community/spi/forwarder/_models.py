"""Framework-neutral request/response models for the Forwarder SPI.

The delivery adapter snapshots an incoming request into a :class:`ForwardRequest`
and streams a :class:`ForwardResponse` back; neither type depends on a web
framework, so flavors (bare httpx, enterprise) are interchangeable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field

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


def strip_hop_by_hop(headers: Mapping[str, str]) -> dict[str, str]:
    """Return *headers* without hop-by-hop entries (case-insensitive)."""
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_HEADERS}


@dataclass(frozen=True)
class ForwardRequest:
    """A request to forward to an upstream, addressed by absolute URL."""

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""


@dataclass
class ForwardResponse:
    """An upstream response, its body streamed as raw byte chunks."""

    status_code: int
    headers: dict[str, str]
    body: AsyncIterator[bytes]
