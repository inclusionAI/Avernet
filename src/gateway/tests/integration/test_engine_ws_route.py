"""Integration tests for the `/engine` WebSocket relay.

Wires the real endpoint to a stub upstream through ``TestClient``, so the
handshake, the rewrite, the authentication seam and the duplex pump are all
exercised together.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gateway.community.adapters.web._engine_ws import (
    ENGINE_WS_ROUTE,
    forward_websocket,
)
from gateway.community.adapters.web._forward import _ALL_METHODS, forward_request
from gateway.community.core.forwarding import DomainMap, build_engine_route
from gateway.community.spi.auth import AuthError
from gateway.community.spi.ws_forwarder import (
    WebSocketClosedError,
    WebSocketForwardRequest,
    WebSocketUpstream,
)


class _StubUpstream(WebSocketUpstream):
    """An echoing upstream that records what the gateway handed it."""

    def __init__(self, request: WebSocketForwardRequest, subprotocol: str) -> None:
        self.request = request
        self._subprotocol = subprotocol
        self._inbox: asyncio.Queue[str | bytes | WebSocketClosedError] = asyncio.Queue()
        self.sent: list[str | bytes] = []
        self.closed_with: tuple[int, str] | None = None

    @property
    def subprotocol(self) -> str:
        return self._subprotocol

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)
        if isinstance(message, str):
            await self._inbox.put(f"echo:{message}")
        else:
            await self._inbox.put(b"echo:" + message)

    async def receive(self) -> str | bytes:
        received = await self._inbox.get()
        if isinstance(received, WebSocketClosedError):
            raise received
        return received

    async def close(self, code: int, reason: str) -> None:
        self.closed_with = (code, reason)

    def close_soon(self, code: int, reason: str) -> None:
        """Make the next queued read report the upstream closing."""
        self._inbox.put_nowait(WebSocketClosedError(code, reason))


class _StubForwarder:
    def __init__(self, *, subprotocol: str = "", fail: bool = False) -> None:
        self.subprotocol = subprotocol
        self.fail = fail
        self.opened: list[_StubUpstream] = []
        self.released: list[_StubUpstream] = []

    @asynccontextmanager
    async def connect(
        self, request: WebSocketForwardRequest
    ) -> AsyncIterator[WebSocketUpstream]:
        if self.fail:
            raise ConnectionRefusedError("upstream down")
        upstream = _StubUpstream(request, self.subprotocol)
        self.opened.append(upstream)
        try:
            yield upstream
        finally:
            self.released.append(upstream)


def _settled(forwarder: _StubForwarder, timeout: float = 5.0) -> None:
    """Wait until the endpoint has released the upstream it opened.

    Also the assertion that nothing outlives the socket: the context manager
    exits only after the relay's tasks have been cancelled and awaited, so a
    leaked pump or a held-open upstream shows up here as a timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if forwarder.released:
            return
        time.sleep(0.005)
    raise AssertionError("the engine socket endpoint did not release its upstream")


class _FakeAuth:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = False
        self.identities: dict = {}

    async def authenticate(self, method: str, path: str, bundle: object) -> dict:
        self.calls.append((method, path))
        if self.fail:
            raise AuthError("unauthorized")
        return self.identities


class _FixedSigner:
    async def sign(self, principals: dict, *, audience: str) -> str:
        return f"signed-for-{audience}"


def _build(
    *,
    forwarder: _StubForwarder | None = None,
    engine_configured: bool = True,
) -> tuple[FastAPI, _FakeAuth, _StubForwarder]:
    app = FastAPI()
    ws_forwarder = forwarder or _StubForwarder()
    auth = _FakeAuth()
    app.state.authenticator = auth
    app.state.principal_signer = _FixedSigner()
    app.state.ws_forwarder = ws_forwarder
    app.state.engine_route = (
        build_engine_route(
            {
                "engine": {"server": "engine_proxy"},
                "servers": {"engine_proxy": {"base_url": "https://proxy.internal"}},
            },
            variables={},
        )
        if engine_configured
        else None
    )
    app.state.domain_map = DomainMap.from_config(
        {
            "domains": {"bots": {"server": "up"}},
            "servers": {"up": {"base_url": "http://upstream"}},
        },
        variables={},
    )
    app.state.forwarder = None
    app.add_api_websocket_route(ENGINE_WS_ROUTE, forward_websocket)
    app.add_api_route("/{full_path:path}", forward_request, methods=_ALL_METHODS)
    return app, auth, ws_forwarder


_PATH = "/engine/ARCA_x@0:20003/api/openclaw/ws?x-proxypass-token=t.o.k"


# ── the happy path ───────────────────────────────────────────────────────────


def test_frames_relay_in_both_directions() -> None:
    app, _, forwarder = _build()
    with TestClient(app) as client:
        with client.websocket_connect(_PATH) as ws:
            ws.send_text("hello")
            assert ws.receive_text() == "echo:hello"
            ws.send_bytes(b"\x00\xff")
            assert ws.receive_bytes() == b"echo:\x00\xff"
            ws.close(1000)
            _settled(forwarder)
    assert forwarder.opened[0].sent == ["hello", b"\x00\xff"]


def test_the_prefix_is_rewritten_onto_proxypass_verbatim() -> None:
    app, _, forwarder = _build()
    with TestClient(app) as client:
        with client.websocket_connect(_PATH) as ws:
            ws.close(1000)
            _settled(forwarder)
    assert forwarder.opened[0].request.url == (
        "wss://proxy.internal/proxypass/ARCA_x@0:20003/api/openclaw/ws"
        "?x-proxypass-token=t.o.k"
    )


def test_an_encoded_target_is_not_decoded_on_the_way_through() -> None:
    app, _, forwarder = _build()
    with TestClient(app) as client:
        with client.websocket_connect("/engine/a%2Fb/api/openclaw/ws") as ws:
            ws.close(1000)
            _settled(forwarder)
    # %2F must not become a path separator on the upstream.
    assert forwarder.opened[0].request.url == (
        "wss://proxy.internal/proxypass/a%2Fb/api/openclaw/ws"
    )


def test_handshake_headers_are_stripped_before_forwarding() -> None:
    app, _, forwarder = _build()
    with TestClient(app) as client:
        with client.websocket_connect(
            _PATH,
            headers={
                "x-keep": "1",
                "x-avernet-principal": "forged",
                "sec-websocket-key": "spoofed",
            },
        ) as ws:
            ws.close(1000)
            _settled(forwarder)
    headers = forwarder.opened[0].request.headers
    assert headers["x-keep"] == "1"
    assert "host" not in headers
    assert "connection" not in headers
    assert "sec-websocket-key" not in headers
    assert "x-avernet-principal" not in headers


def test_a_resolved_identity_is_signed_onto_the_upstream_handshake() -> None:
    app, auth, forwarder = _build()
    auth.identities = {"user": object()}
    with TestClient(app) as client:
        with client.websocket_connect(_PATH) as ws:
            ws.close(1000)
            _settled(forwarder)
    headers = forwarder.opened[0].request.headers
    assert headers["X-Avernet-Principal"] == "signed-for-engine_proxy"


def test_the_upstream_subprotocol_is_echoed_to_the_client() -> None:
    app, _, forwarder = _build(forwarder=_StubForwarder(subprotocol="chat"))
    with TestClient(app) as client:
        with client.websocket_connect(_PATH, subprotocols=["chat"]) as ws:
            assert ws.accepted_subprotocol == "chat"
            ws.close(1000)
            _settled(forwarder)


def test_route_security_is_consulted_for_the_handshake() -> None:
    app, auth, forwarder = _build()
    with TestClient(app) as client:
        with client.websocket_connect(_PATH) as ws:
            ws.close(1000)
            _settled(forwarder)
    assert auth.calls == [("GET", "/engine/ARCA_x@0:20003/api/openclaw/ws")]


# ── closing ──────────────────────────────────────────────────────────────────


def test_a_client_close_is_carried_upstream() -> None:
    app, _, forwarder = _build()
    with TestClient(app) as client:
        with client.websocket_connect(_PATH) as ws:
            ws.close(code=4001, reason="client done")
            _settled(forwarder)
    assert forwarder.opened[0].closed_with == (4001, "client done")


def test_an_upstream_close_is_carried_to_the_client() -> None:
    forwarder = _StubForwarder()
    app, _, _ = _build(forwarder=forwarder)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(_PATH) as ws:
                forwarder.opened[0].close_soon(4200, "upstream done")
                ws.receive_text()
    assert caught.value.code == 4200
    assert caught.value.reason == "upstream done"


# ── refusals ─────────────────────────────────────────────────────────────────


def test_no_engine_route_configured_refuses_the_handshake() -> None:
    app, auth, forwarder = _build(engine_configured=False)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(_PATH):
                pass
    assert caught.value.code == 4404
    assert auth.calls == []  # refused before authenticating
    assert forwarder.opened == []


def test_an_authentication_failure_refuses_the_handshake() -> None:
    app, auth, forwarder = _build()
    auth.fail = True
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(_PATH):
                pass
    assert caught.value.code == 4401
    assert forwarder.opened == []  # never dialled


def test_an_unreachable_upstream_refuses_the_handshake() -> None:
    app, _, _ = _build(forwarder=_StubForwarder(fail=True))
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(_PATH):
                pass
    # The client is never accepted onto a socket the gateway cannot serve.
    assert caught.value.code == 4502


# ── the prefix publishes a socket and nothing else ───────────────────────────


def test_http_under_the_engine_prefix_is_still_an_unknown_route() -> None:
    app, _, _ = _build()
    with TestClient(app) as client:
        response = client.get("/engine/ARCA_x@0:20003/api/openclaw/ws")
    assert response.status_code == 404
    assert response.json()["code"] == 404001
