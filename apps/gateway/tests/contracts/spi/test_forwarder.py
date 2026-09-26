"""Conformance test for the Forwarder SPI (Rule 25).

Uses a real ASGI app via ``httpx.ASGITransport`` so streaming is exercised
end to end (``MockTransport`` pre-materialises the body and can't stream).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Generator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from gateway.community.plugins.forwarder.httpx import HttpxForwarder
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


class _RecordingBody:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = iter(chunks)
        self.closed = False

    def __aiter__(self) -> _RecordingBody:
        return self

    async def __anext__(self) -> bytes:
        if self.closed:
            raise StopAsyncIteration
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.closed = True


class _FailingTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("upstream unavailable", request=request)


class _BlockingTransport(httpx.AsyncBaseTransport):
    def __init__(self, send_started: asyncio.Event) -> None:
        self._send_started = send_started

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._send_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _RecordingResponseStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class _BlockingResponseStream(httpx.AsyncByteStream):
    def __init__(self, read_started: asyncio.Event) -> None:
        self._read_started = read_started
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self._read_started.set()
        await asyncio.Event().wait()
        yield b"unreachable"

    async def aclose(self) -> None:
        self.closed = True


class _EarlyResponseTransport(httpx.AsyncBaseTransport):
    def __init__(self, response_stream: _RecordingResponseStream) -> None:
        self._response_stream = response_stream

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(413, request=request, stream=self._response_stream)


class _ChallengeAuth(httpx.Auth):
    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        response = yield request
        if response.status_code == 401:
            request.headers["authorization"] = "Bearer challenge-response"
            yield request


@dataclass(frozen=True)
class _SeenRequest:
    path: str
    headers: dict[str, str]
    body: bytes


_ResponseFactory = Callable[[_SeenRequest], tuple[int, list[tuple[bytes, bytes]]]]


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


def _record_requests(
    seen: list[_SeenRequest],
    response: _ResponseFactory = lambda _: (204, []),
) -> Callable[[Scope, Receive, Send], Awaitable[None]]:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        request = _SeenRequest(
            path=scope["path"],
            headers={key.decode(): value.decode() for key, value in scope["headers"]},
            body=bytes(body),
        )
        seen.append(request)
        status, headers = response(request)
        await send(
            {"type": "http.response.start", "status": status, "headers": headers}
        )
        await send({"type": "http.response.body", "body": b""})

    return app


async def _forward_to_asgi(
    request: ForwardRequest,
    app: Callable[[Scope, Receive, Send], Awaitable[None]],
    **client_options: Any,
) -> tuple[int, list[tuple[str, str]]]:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, **client_options) as client:
        forwarder = HttpxForwarder(client=client)
        async with forwarder.forward(request) as response:
            _ = [chunk async for chunk in response.body]
            return response.status_code, response.headers


async def test_forward_streams_multichunk_body_and_status() -> None:
    seen: dict[str, str] = {}
    app = _app(seen, chunks=[b"data: a\n\n", b"data: b\n\n"])
    async with _client(app) as client:
        forwarder: Forwarder = HttpxForwarder(client=client)
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
        forwarder = HttpxForwarder(client=client)
        req = ForwardRequest(
            method="POST",
            url="http://up/openapi/v1/bots",
            headers={"te": "trailers", "x-a": "1"},
            body=_RecordingBody([b"{}"]),
        )
        async with forwarder.forward(req) as response:
            _ = [chunk async for chunk in response.body]
    assert "te" not in seen
    assert seen["x-a"] == "1"


@pytest.mark.parametrize(
    ("method", "headers", "chunks", "expected_body", "expected_framing"),
    [
        (
            "PUT",
            {"content-length": "6"},
            [b"abc", b"def"],
            b"abcdef",
            {"content-length": "6", "transfer-encoding": None},
        ),
        (
            "POST",
            {},
            [b"abc", b"def"],
            b"abcdef",
            {"content-length": None, "transfer-encoding": "chunked"},
        ),
        (
            "POST",
            {},
            None,
            b"",
            {"content-length": "0", "transfer-encoding": None},
        ),
    ],
    ids=["declared-length", "unknown-length", "empty"],
)
async def test_request_body_framing(
    method: str,
    headers: dict[str, str],
    chunks: list[bytes] | None,
    expected_body: bytes,
    expected_framing: dict[str, str | None],
) -> None:
    seen: list[_SeenRequest] = []
    body = _RecordingBody(chunks) if chunks is not None else None
    await _forward_to_asgi(
        ForwardRequest(
            method=method,
            url="http://up/upload",
            headers=headers,
            body=body,
        ),
        _record_requests(seen),
    )

    assert seen[0].body == expected_body
    assert {
        name: seen[0].headers.get(name) for name in expected_framing
    } == expected_framing
    if body is not None:
        assert body.closed


async def test_one_shot_request_body_is_not_replayed_across_redirects() -> None:
    seen: list[_SeenRequest] = []

    def redirect(request: _SeenRequest) -> tuple[int, list[tuple[bytes, bytes]]]:
        return (
            (307, [(b"location", b"/target")])
            if request.path == "/source"
            else (200, [])
        )

    status, _ = await _forward_to_asgi(
        ForwardRequest(
            method="PUT",
            url="http://up/source",
            headers={"content-length": "7"},
            body=_RecordingBody([b"payload"]),
        ),
        _record_requests(seen, redirect),
        follow_redirects=True,
    )

    assert status == 307
    assert [(request.path, request.body) for request in seen] == [
        ("/source", b"payload")
    ]


async def test_one_shot_request_body_is_not_replayed_by_client_auth() -> None:
    seen: list[_SeenRequest] = []

    def challenge(request: _SeenRequest) -> tuple[int, list[tuple[bytes, bytes]]]:
        status = (
            200
            if request.headers.get("authorization") == "Bearer challenge-response"
            else 401
        )
        return status, []

    status, _ = await _forward_to_asgi(
        ForwardRequest(
            method="PUT",
            url="http://up/upload",
            headers={"content-length": "7"},
            body=_RecordingBody([b"payload"]),
        ),
        _record_requests(seen, challenge),
        auth=_ChallengeAuth(),
    )

    assert status == 401
    assert [
        (request.headers.get("authorization"), request.body) for request in seen
    ] == [(None, b"payload")]


@pytest.mark.parametrize(
    ("url", "transport", "error"),
    [
        ("http://up/upload", _FailingTransport(), httpx.ConnectError),
        ("http://[::1", _FailingTransport(), httpx.InvalidURL),
    ],
    ids=["transport-failure", "invalid-metadata"],
)
async def test_request_body_closes_when_send_fails(
    url: str,
    transport: httpx.AsyncBaseTransport,
    error: type[Exception],
) -> None:
    body = _RecordingBody([b"payload"])
    async with httpx.AsyncClient(transport=transport) as client:
        forwarder = HttpxForwarder(client=client)
        with pytest.raises(error):
            async with forwarder.forward(
                ForwardRequest(method="POST", url=url, body=body)
            ):
                pass
    assert body.closed


async def test_upstream_early_response_closes_both_streams() -> None:
    body = _RecordingBody([b"not-consumed"])
    response_stream = _RecordingResponseStream([b"too large"])
    async with httpx.AsyncClient(
        transport=_EarlyResponseTransport(response_stream)
    ) as client:
        forwarder = HttpxForwarder(client=client)
        async with forwarder.forward(
            ForwardRequest(method="POST", url="http://up/upload", body=body)
        ) as response:
            assert response.status_code == 413

    assert body.closed
    assert response_stream.closed


async def test_cancelled_response_consumer_closes_upstream_stream() -> None:
    read_started = asyncio.Event()
    response_stream = _BlockingResponseStream(read_started)
    async with httpx.AsyncClient(
        transport=_EarlyResponseTransport(response_stream)
    ) as client:
        forwarder = HttpxForwarder(client=client)
        context = forwarder.forward(
            ForwardRequest(method="GET", url="http://up/download")
        )
        response = await context.__aenter__()
        reading = asyncio.create_task(anext(response.body))
        await asyncio.wait_for(read_started.wait(), timeout=1)
        reading.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reading
        await context.__aexit__(None, None, None)

    assert response_stream.closed


async def test_request_body_closes_when_upstream_send_is_cancelled() -> None:
    send_started = asyncio.Event()
    body = _RecordingBody([b"payload"])
    async with httpx.AsyncClient(transport=_BlockingTransport(send_started)) as client:
        forwarder = HttpxForwarder(client=client)
        context = forwarder.forward(
            ForwardRequest(method="POST", url="http://up/upload", body=body)
        )
        entering = asyncio.create_task(context.__aenter__())
        await asyncio.wait_for(send_started.wait(), timeout=1)
        entering.cancel()
        with pytest.raises(asyncio.CancelledError):
            await entering

    assert body.closed


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
        forwarder = HttpxForwarder(client=client)
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
        forwarder = HttpxForwarder(client=client)
        async with forwarder.forward(
            ForwardRequest(method="GET", url="http://up/openapi/v1/bots")
        ) as response:
            cookies = [v for k, v in response.headers if k.lower() == "set-cookie"]
            _ = [chunk async for chunk in response.body]
    assert cookies == ["session=abc", "csrf=xyz"]


async def test_bare_forwarder_creates_and_closes_own_client() -> None:
    forwarder = HttpxForwarder()
    client = forwarder._get_client()
    assert client is forwarder._get_client()  # reused
    await forwarder.aclose()
