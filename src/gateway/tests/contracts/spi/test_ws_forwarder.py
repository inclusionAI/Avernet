"""Conformance test for the WebSocketForwarder SPI (Rule 25).

Runs the community flavor against a **real** ``websockets`` server on a loopback
port, because everything this seam owns — the handshake, subprotocol
negotiation, frame kinds, close codes — is protocol behaviour a stub cannot
stand in for.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator

import pytest
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from gateway.community.plugins.ws_forwarder.websockets import (
    WebsocketsForwarder,
    _plugin,
)
from gateway.community.spi.ws_forwarder import (
    WEBSOCKET_HANDSHAKE_HEADERS,
    WebSocketClosedError,
    WebSocketForwarder,
    WebSocketForwardRequest,
)


class _Recorder:
    """The upstream under test: echoes, and remembers what it was handed."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.headers: list[dict[str, str]] = []
        self.close_code = 0
        self.close_reason = ""
        self.closed = asyncio.Event()

    async def handler(self, connection: ServerConnection) -> None:
        self.paths.append(connection.request.path if connection.request else "")
        self.headers.append(
            {
                k.lower(): v
                for k, v in (
                    connection.request.headers.raw_items() if connection.request else []
                )
            }
        )
        try:
            async for message in connection:
                if isinstance(message, str):
                    if message == "bye":
                        await connection.close(4200, "server said so")
                        return
                    await connection.send(f"echo:{message}")
                else:
                    await connection.send(b"echo:" + message)
        except ConnectionClosed as exc:
            close = exc.rcvd or exc.sent
            if close is not None:
                self.close_code, self.close_reason = close.code, close.reason
        finally:
            self.closed.set()


def _select_subprotocol(connection, offered):  # noqa: ANN001, ANN202
    """Accept ``chat`` when offered, and accept a handshake offering nothing.

    The library's default refuses a client that offers no subprotocol once the
    server declares one; a real engine socket is opened both ways, so the test
    upstream must be too.
    """
    return "chat" if "chat" in offered else None


@pytest.fixture
async def upstream() -> AsyncIterator[tuple[_Recorder, str]]:
    recorder = _Recorder()
    async with serve(
        recorder.handler,
        "127.0.0.1",
        0,
        subprotocols=["chat"],
        select_subprotocol=_select_subprotocol,
    ) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        yield recorder, f"ws://127.0.0.1:{port}"


def _forwarder() -> WebSocketForwarder:
    return WebsocketsForwarder()


async def test_text_frames_round_trip(upstream) -> None:  # noqa: ANN001
    _, base = upstream
    async with _forwarder().connect(
        WebSocketForwardRequest(url=f"{base}/proxypass/t/api/openclaw/ws")
    ) as ws:
        await ws.send("hello")
        assert await ws.receive() == "echo:hello"


async def test_binary_frames_stay_binary(upstream) -> None:  # noqa: ANN001
    _, base = upstream
    async with _forwarder().connect(
        WebSocketForwardRequest(url=f"{base}/proxypass/t")
    ) as ws:
        await ws.send(b"\x00\xff")
        received = await ws.receive()
    assert received == b"echo:\x00\xff"
    assert isinstance(received, bytes)


async def test_path_and_query_reach_the_upstream_verbatim(upstream) -> None:  # noqa: ANN001
    recorder, base = upstream
    target = "/proxypass/ARCA_x%400%3A20003/api/openclaw/ws?x-proxypass-token=t.o.k"
    async with _forwarder().connect(WebSocketForwardRequest(url=f"{base}{target}")):
        pass
    assert recorder.paths == [target]


async def test_headers_are_forwarded_onto_the_handshake(upstream) -> None:  # noqa: ANN001
    recorder, base = upstream
    async with _forwarder().connect(
        WebSocketForwardRequest(
            url=f"{base}/proxypass/t", headers={"x-avernet-principal": "signed"}
        )
    ):
        pass
    assert recorder.headers[0]["x-avernet-principal"] == "signed"


async def test_subprotocol_is_offered_and_reported(upstream) -> None:  # noqa: ANN001
    _, base = upstream
    async with _forwarder().connect(
        WebSocketForwardRequest(url=f"{base}/proxypass/t", subprotocols=("chat",))
    ) as ws:
        assert ws.subprotocol == "chat"


async def test_no_subprotocol_reports_the_empty_string(upstream) -> None:  # noqa: ANN001
    _, base = upstream
    async with _forwarder().connect(
        WebSocketForwardRequest(url=f"{base}/proxypass/t")
    ) as ws:
        assert ws.subprotocol == ""


async def test_upstream_close_surfaces_as_the_spi_error(upstream) -> None:  # noqa: ANN001
    _, base = upstream
    async with _forwarder().connect(
        WebSocketForwardRequest(url=f"{base}/proxypass/t")
    ) as ws:
        await ws.send("bye")
        with pytest.raises(WebSocketClosedError) as caught:
            await ws.receive()
    assert caught.value.code == 4200
    assert caught.value.reason == "server said so"


async def test_close_carries_the_code_and_reason_upstream(upstream) -> None:  # noqa: ANN001
    recorder, base = upstream
    async with _forwarder().connect(
        WebSocketForwardRequest(url=f"{base}/proxypass/t")
    ) as ws:
        await ws.close(4001, "client went away")
    await asyncio.wait_for(recorder.closed.wait(), timeout=5)
    assert (recorder.close_code, recorder.close_reason) == (4001, "client went away")


async def test_an_unreachable_upstream_raises_rather_than_yielding(upstream) -> None:  # noqa: ANN001
    _, base = upstream
    dead = base.rsplit(":", 1)[0] + ":1"
    with pytest.raises(Exception):
        async with _forwarder().connect(WebSocketForwardRequest(url=f"{dead}/x")):
            pass


async def test_no_read_deadline_is_imposed(upstream) -> None:  # noqa: ANN001
    """A quiet socket must survive: the credential is checked once, at open.

    Asserted structurally as well as behaviourally — a receive that simply has
    not timed out yet proves little on its own, so the dial is also inspected
    for any parameter that would bound a read.
    """
    _, base = upstream
    async with _forwarder().connect(
        WebSocketForwardRequest(url=f"{base}/proxypass/t")
    ) as ws:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ws.receive(), timeout=0.25)
        # Still usable after the idle period — nothing tore it down.
        await ws.send("still here")
        assert await ws.receive() == "echo:still here"

    source = inspect.getsource(_plugin.WebsocketsForwarder.connect)
    assert "max_size=None" in source
    assert "close_timeout" not in source
    assert "ping_timeout" not in source


def test_handshake_headers_the_client_composes_are_declared() -> None:
    """The adapter strips these; the set lives beside the SPI it belongs to."""
    assert "sec-websocket-key" in WEBSOCKET_HANDSHAKE_HEADERS
    assert "sec-websocket-protocol" in WEBSOCKET_HANDSHAKE_HEADERS
    assert "authorization" not in WEBSOCKET_HANDSHAKE_HEADERS
