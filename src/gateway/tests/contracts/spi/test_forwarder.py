"""Conformance test for the Forwarder SPI (Rule 25).

Uses a real ASGI app via ``httpx.ASGITransport`` so streaming is exercised
end to end (``MockTransport`` pre-materialises the body and can't stream).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from gateway.community.plugins.forwarder.bare import BareForwarder
from gateway.community.spi.forwarder import (
    Forwarder,
    ForwardRequest,
    ForwardResponse,
    strip_hop_by_hop,
    strip_hop_by_hop_items,
)

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


def test_strip_hop_by_hop_removes_connection_headers() -> None:
    cleaned = strip_hop_by_hop(
        {"Connection": "keep-alive", "Transfer-Encoding": "chunked", "X-Keep": "1"}
    )
    assert cleaned == {"X-Keep": "1"}


def test_strip_hop_by_hop_drops_connection_named_headers() -> None:
    # A header named in the Connection value is connection-scoped (RFC 7230 §6.1).
    cleaned = strip_hop_by_hop(
        {"Connection": "X-Internal", "X-Internal": "secret", "X-Keep": "1"}
    )
    assert cleaned == {"X-Keep": "1"}


def test_strip_hop_by_hop_items_preserves_duplicate_headers() -> None:
    kept = strip_hop_by_hop_items(
        [
            ("set-cookie", "a=1"),
            ("connection", "close"),
            ("set-cookie", "b=2"),
        ]
    )
    assert kept == [("set-cookie", "a=1"), ("set-cookie", "b=2")]


def _app(
    seen_headers: dict[str, str],
    *,
    status: int = 200,
    resp_headers: list[tuple[bytes, bytes]] | None = None,
    chunks: list[bytes] | None = None,
) -> Callable[[Scope, Receive, Send], Awaitable[None]]:
    """A tiny ASGI app that records request headers and streams a reply."""

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        for k, v in scope["headers"]:
            seen_headers[k.decode()] = v.decode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": resp_headers or [(b"content-type", b"application/json")],
            }
        )
        body_chunks = chunks if chunks is not None else [b'{"ok":true}']
        for i, chunk in enumerate(body_chunks):
            await send(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": i < len(body_chunks) - 1,
                }
            )

    return app


def _client(
    app: Callable[[Scope, Receive, Send], Awaitable[None]],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app))  # type: ignore[arg-type]


async def test_forward_streams_multichunk_body_and_status() -> None:
    seen: dict[str, str] = {}
    app = _app(seen, chunks=[b"data: a\n\n", b"data: b\n\n"])
    async with _client(app) as client:
        forwarder: Forwarder = BareForwarder(client=client)
        async with forwarder.forward(
            ForwardRequest(method="GET", url="http://up/openapi/v1/bots/1")
        ) as response:
            assert isinstance(response, ForwardResponse)
            assert response.status_code == 200
            body = b"".join([chunk async for chunk in response.body])
    assert body == b"data: a\n\ndata: b\n\n"


async def test_request_hop_by_hop_headers_stripped_before_send() -> None:
    # `te` is hop-by-hop and — unlike `connection` — not re-added by httpx for
    # its own hop, so we can observe that the caller's copy was dropped.
    seen: dict[str, str] = {}
    async with _client(_app(seen)) as client:
        forwarder = BareForwarder(client=client)
        req = ForwardRequest(
            method="POST",
            url="http://up/openapi/v1/bots",
            headers={"te": "trailers", "x-a": "1"},
            content=b"{}",
        )
        async with forwarder.forward(req) as response:
            _ = [chunk async for chunk in response.body]
    assert "te" not in seen
    assert seen["x-a"] == "1"


async def test_response_hop_by_hop_headers_stripped() -> None:
    seen: dict[str, str] = {}
    app = _app(
        seen,
        resp_headers=[
            (b"content-type", b"application/json"),
            (b"connection", b"close"),
        ],
    )
    async with _client(app) as client:
        forwarder = BareForwarder(client=client)
        async with forwarder.forward(
            ForwardRequest(method="GET", url="http://up/openapi/v1/bots")
        ) as response:
            names = {k.lower() for k, _ in response.headers}
            assert "connection" not in names
            assert ("content-type", "application/json") in response.headers
            _ = [chunk async for chunk in response.body]


async def test_response_preserves_duplicate_set_cookie() -> None:
    seen: dict[str, str] = {}
    app = _app(
        seen,
        resp_headers=[
            (b"content-type", b"application/json"),
            (b"set-cookie", b"session=abc"),
            (b"set-cookie", b"csrf=xyz"),
        ],
    )
    async with _client(app) as client:
        forwarder = BareForwarder(client=client)
        async with forwarder.forward(
            ForwardRequest(method="GET", url="http://up/openapi/v1/bots")
        ) as response:
            cookies = [v for k, v in response.headers if k.lower() == "set-cookie"]
            _ = [chunk async for chunk in response.body]
    assert cookies == ["session=abc", "csrf=xyz"]


async def test_bare_forwarder_creates_and_closes_own_client() -> None:
    forwarder = BareForwarder()
    client = forwarder._get_client()
    assert client is forwarder._get_client()  # reused
    await forwarder.aclose()
