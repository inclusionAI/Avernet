"""Integration tests for the WebSocket relay entrypoint.

Wires the real endpoint to a stub upstream through ``TestClient``, so domain
resolution, the declared prefix rewrite, the authentication seam and the duplex
pump are all exercised together. The `engine` domain is the worked example; the
entrypoint itself names no domain.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from gateway.community.adapters.web._forward import _ALL_METHODS, forward_request
from gateway.community.adapters.web._relay_ws import (
    _has_dot_segment,
    forward_websocket,
    relay_routes,
)
from gateway.community.core.forwarding import DomainMap
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
        #: None until the test makes the *send* direction the one that notices
        #: the upstream closing, which is the race the receive pump usually wins.
        self._send_closes_with: tuple[int, str] | None = None

    @property
    def subprotocol(self) -> str:
        return self._subprotocol

    async def send(self, message: str | bytes) -> None:
        if self._send_closes_with is not None:
            raise WebSocketClosedError(*self._send_closes_with)
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

    def close_on_send(self, code: int, reason: str) -> None:
        """Make the next *write* report the upstream closing, leaving reads blocked."""
        self._send_closes_with = (code, reason)


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
    raise AssertionError("the relay endpoint did not release its upstream")


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


_ENGINE_DOMAIN = {
    "server": "engine_proxy",
    "protocols": ["websocket"],
    "rewrite": {"from": "/openapi/v1/engine", "to": "/proxypass"},
}


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

    domains: dict[str, object] = {"bots": {"server": "up"}}
    if engine_configured:
        domains["engine"] = _ENGINE_DOMAIN
    domain_map = DomainMap.from_config(
        {
            "domains": domains,
            "servers": {
                "up": {"base_url": "http://upstream"},
                "engine_proxy": {"base_url": "https://proxy.internal"},
            },
        },
        variables={},
    )
    app.state.domain_map = domain_map
    app.state.forwarder = None

    # Mounted the way the composition root does it: one route per socket domain,
    # driven from config. With no engine domain configured, nothing is mounted
    # under that prefix at all — which is the behaviour under test.
    for name in domain_map.websocket_domains():
        for route in relay_routes(domain_map.base_path, name):
            app.add_api_websocket_route(route, forward_websocket)
    # An always-present route so an unconfigured prefix is refused by the
    # endpoint rather than by Starlette's router, keeping the assertion about
    # our own behaviour.
    app.add_api_websocket_route("/{full_path:path}", forward_websocket)
    app.add_api_route("/{full_path:path}", forward_request, methods=_ALL_METHODS)
    return app, auth, ws_forwarder


_PATH = "/openapi/v1/engine/ARCA_x@0:20003/api/openclaw/ws?x-proxypass-token=t.o.k"


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
        with client.websocket_connect("/openapi/v1/engine/a%2Fb/api/openclaw/ws") as ws:
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
    assert auth.calls == [("GET", "/openapi/v1/engine/ARCA_x@0:20003/api/openclaw/ws")]


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


def test_an_upstream_close_noticed_while_sending_is_carried_to_the_client() -> None:
    """The send pump can be the one that notices, and it must decide the close too.

    A client writing while the upstream closes races the receive pump. If the
    write wins, the relay must still relay the peer's code — reporting a
    gateway-side 1011 would blame the gateway for the upstream's goodbye.
    """
    forwarder = _StubForwarder()
    app, _, _ = _build(forwarder=forwarder)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(_PATH) as ws:
                forwarder.opened[0].close_on_send(4200, "upstream done")
                ws.send_text("a frame sent while it was closing")
                ws.receive_text()
    assert caught.value.code == 4200
    assert caught.value.reason == "upstream done"


# ── the client that leaves while the gateway is dialling ─────────────────────


async def test_a_client_that_leaves_before_accept_still_releases_the_upstream() -> None:
    """The dial wins the race, the caller does not wait for it.

    The gateway opens the upstream *before* accepting, so a client that gives up
    while that dial is in flight leaves an upstream already open and a socket
    with nobody behind it. Both have to be let go: the upstream because it was
    entered, and the handshake because there is no longer anyone to accept.

    Driven at the ASGI seam rather than through ``TestClient``, because the
    condition under test is a message ordering — ``websocket.disconnect``
    arriving where ``websocket.connect`` is expected — that a cooperating test
    client will not produce.
    """
    app, _, forwarder = _build()
    inbox: asyncio.Queue[dict] = asyncio.Queue()
    inbox.put_nowait({"type": "websocket.disconnect", "code": 1006})
    sent: list[dict] = []

    async def _send(message: dict) -> None:
        sent.append(message)

    path, _, query = _PATH.partition("?")
    websocket = WebSocket(
        {
            "type": "websocket",
            "app": app,
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "headers": [],
            "subprotocols": [],
        },
        receive=inbox.get,
        send=_send,
    )

    # No exception escapes: an abandoned handshake is a race, not a fault, and
    # a traceback per occurrence would bury the log line that reports a real
    # upstream failure.
    await forward_websocket(websocket)

    assert forwarder.opened, "the upstream was dialled before the client was accepted"
    assert forwarder.released == forwarder.opened, "the open upstream was released"
    assert sent == [], "nothing was written to a client that had already gone"


# ── refusals ─────────────────────────────────────────────────────────────────


def test_a_domain_that_is_not_configured_refuses_the_handshake() -> None:
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


def test_http_on_a_socket_only_domain_is_an_unknown_route() -> None:
    """The domain resolves, but it does not answer this plane."""
    app, _, forwarder = _build()
    with TestClient(app) as client:
        response = client.get("/openapi/v1/engine/ARCA_x@0:20003/api/openclaw/ws")
    assert response.status_code == 404
    assert response.json()["code"] == 404001
    assert forwarder.opened == []  # never dialled the upstream


def test_a_traversal_under_the_socket_domain_never_reaches_http() -> None:
    """`%2e%2e` decodes to `..`, which httpx would collapse — but the HTTP
    plane refuses this domain outright, so there is nothing to collapse."""
    app, _, forwarder = _build()
    with TestClient(app) as client:
        response = client.get("/openapi/v1/engine/%2e%2e/%2e%2e/admin/keys")
    assert response.status_code == 404
    assert forwarder.opened == []


@pytest.mark.parametrize(
    "decoded_path",
    [
        "/openapi/v1/engine/../../admin",
        "/openapi/v1/engine/./admin",
        "/openapi/v1/engine/a/../../admin",
    ],
)
def test_the_traversal_guard_matches_decoded_dot_segments(decoded_path: str) -> None:
    """Asserted directly, because no HTTP client will send these unaltered.

    httpx normalises a literal `..` before the request leaves it, so the route
    test below can only exercise the percent-encoded spellings. The guard reads
    the decoded path, so these are the shapes it actually compares against.
    """
    assert _has_dot_segment(decoded_path)


@pytest.mark.parametrize(
    "decoded_path",
    [
        "/openapi/v1/engine/a.b/ws",  # a dot inside a name is not a dot segment
        "/openapi/v1/engine/%2e/ws",  # what `%252e` decodes to: a filename
        "/openapi/v1/engine/.../ws",
        "/openapi/v1/engine/..a/ws",
    ],
)
def test_the_traversal_guard_leaves_ordinary_paths_alone(decoded_path: str) -> None:
    assert not _has_dot_segment(decoded_path)


@pytest.mark.parametrize(
    "path",
    [
        "/openapi/v1/engine/%2e%2e/%2e%2e/admin",
        "/openapi/v1/engine/%2E%2E/admin",  # encoding is case-insensitive
        "/openapi/v1/engine/.%2e/admin",  # half-encoded
        "/openapi/v1/engine/%2e%2e%2Fadmin",  # an encoded slash hiding the boundary
    ],
)
def test_a_traversal_on_the_socket_plane_is_refused_before_dialling(path: str) -> None:
    """The one prefix the shipped table exempts from authentication.

    The socket plane relays the *raw* path by design, so `%2e%2e` would reach
    the upstream intact. The hop behind the gateway checks a credential scoped
    to `/proxypass`; an upstream — or any L7 hop between — that decodes and then
    normalises would resolve the traversal outside that route, on a host the
    gateway is configured to reach. Refusing here does not require guessing
    which of them normalises.
    """
    app, _, forwarder = _build()
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(path):
                pass
    assert caught.value.code == 4400
    assert forwarder.opened == []  # never dialled


@pytest.mark.parametrize(
    "path",
    [
        "/openapi/v1/%65ngine/t/ws",  # 'e' encoded — decodes to the domain
        "/openapi/v1/engin%65/t/ws",
        "/openapi/v1/engine%2Ft/ws",  # the separator itself encoded
        "/openapi%2Fv1/engine/t/ws",
    ],
)
def test_an_encoded_routing_prefix_is_refused(path: str) -> None:
    """Routing reads the decoded path; the dial is built from the raw one.

    Encoding a character of the prefix makes those two disagree: the decoded
    path resolves to the anonymous `engine` domain, while the raw path no longer
    carries the rewrite's literal `from`, so the rewrite does not fire and the
    upstream is dialled *outside* `/proxypass` — the prefix whose credential
    check the whole design leans on. One request must not be authorised as one
    resource and dialled as another.
    """
    app, _, forwarder = _build()
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(path):
                pass
    assert caught.value.code == 4400
    assert forwarder.opened == []  # never dialled


def test_the_bare_domain_prefix_is_mounted_too() -> None:
    """Starlette needs a separator before `{full_path:path}`, so two mounts.

    Asserted structurally rather than by connecting, and deliberately so:
    `TestClient` follows Starlette's slash redirect, so a handshake to the bare
    prefix succeeds through it whether or not the route exists. Against real
    uvicorn the same request is refused with HTTP 403 before reaching the
    entrypoint. A behavioural test here would pass either way and pin nothing.
    """
    app, _, _ = _build()
    mounted = {getattr(route, "path", None) for route in app.routes}
    assert "/openapi/v1/engine" in mounted
    assert "/openapi/v1/engine/{full_path:path}" in mounted


def test_relay_routes_returns_both_forms_together() -> None:
    """Returned as one tuple so a caller cannot mount one and forget the other."""
    assert relay_routes("/openapi/v1", "engine") == (
        "/openapi/v1/engine",
        "/openapi/v1/engine/{full_path:path}",
    )


def _nested_rewrite_app(forwarder: _StubForwarder) -> FastAPI:
    """A domain whose `rewrite.from` is *deeper* than the domain prefix.

    `_parse_rewrite` accepts this, so the guard has to cover it: the domain
    prefix alone is not what decided the upstream path here.
    """
    from gateway.community.core.forwarding import DomainMap

    domain_map = DomainMap.from_config(
        {
            "domains": {
                "engine": {
                    "server": "p",
                    "protocols": ["websocket"],
                    "rewrite": {"from": "/openapi/v1/engine/v2", "to": "/proxypass"},
                }
            },
            "servers": {"p": {"base_url": "wss://proxy.internal"}},
        },
        variables={},
    )
    app = FastAPI()
    app.state.authenticator = _FakeAuth()
    app.state.principal_signer = _FixedSigner()
    app.state.ws_forwarder = forwarder
    app.state.domain_map = domain_map
    for name in domain_map.websocket_domains():
        for route in relay_routes(domain_map.base_path, name):
            app.add_api_websocket_route(route, forward_websocket)
    return app


@pytest.mark.parametrize(
    "path",
    [
        "/openapi/v1/engine/%76%32/T/ws",  # 'v2' encoded — clears the domain prefix
        "/openapi/v1/engine/v%32/T/ws",
        "/openapi/v1/engine/other/T/ws",  # simply not under the declared `from`
    ],
)
def test_a_raw_path_that_misses_a_nested_rewrite_is_refused(path: str) -> None:
    """The rewrite's own `from` is what decided the upstream path, so it governs.

    `/openapi/v1/engine/%76%32/...` decodes to `/openapi/v1/engine/v2/...`, so it
    resolves and authenticates as the anonymous domain and clears the *domain*
    prefix — but the raw path no longer carries the literal `from`, the
    substitution misses, and the dial lands outside `/proxypass`. Checking only
    the domain prefix was the gap.
    """
    forwarder = _StubForwarder()
    with TestClient(_nested_rewrite_app(forwarder)) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(path):
                pass
    assert caught.value.code == 4400
    assert forwarder.opened == []


def test_a_nested_rewrite_still_relays_its_own_paths() -> None:
    """The tightening must not close the configuration it is guarding."""
    forwarder = _StubForwarder()
    with TestClient(_nested_rewrite_app(forwarder)) as client:
        with client.websocket_connect("/openapi/v1/engine/v2/T%40x/ws") as ws:
            ws.send_text("ping")
            ws.receive_text()
        _settled(forwarder)
    assert forwarder.opened[0].request.url == "wss://proxy.internal/proxypass/T%40x/ws"


def test_an_encoded_tail_still_relays_verbatim() -> None:
    """Only the routing prefix is constrained — the tail may encode freely.

    That is the property the raw-path relay exists to preserve: the routing
    target reaches the upstream exactly as its author wrote it.
    """
    app, _, forwarder = _build()
    with TestClient(app) as client:
        with client.websocket_connect(
            "/openapi/v1/engine/ARCA_x%400%3A20003/api/ws"
        ) as ws:
            ws.send_text("ping")
            ws.receive_text()
        _settled(forwarder)
    assert forwarder.opened[0].request.url == (
        "wss://proxy.internal/proxypass/ARCA_x%400%3A20003/api/ws"
    )


def test_a_dot_inside_a_name_still_relays() -> None:
    """Only whole `.`/`..` segments are traversal; a dot inside a name is not.

    The double-encoded case (`%252e`, which decodes once to the literal text
    `%2e` and names a file) is asserted against the guard directly and in the
    live smoke instead: `TestClient` decodes the path *twice*, so it turns
    `%252e` into `.` and would refuse a request real uvicorn relays.
    """
    app, _, forwarder = _build()
    with TestClient(app) as client:
        with client.websocket_connect("/openapi/v1/engine/a.b/c..d/ws") as ws:
            ws.send_text("ping")
            ws.receive_text()
        _settled(forwarder)
    assert (
        forwarder.opened[0].request.url == "wss://proxy.internal/proxypass/a.b/c..d/ws"
    )


def test_an_http_domain_still_forwards_verbatim() -> None:
    """The default is unchanged: no protocols, no rewrite, path as it arrived."""
    from gateway.community.core.forwarding import DomainMap

    domain_map = DomainMap.from_config(
        {
            "domains": {"bots": {"server": "up"}},
            "servers": {"up": {"base_url": "http://upstream"}},
        },
        variables={},
    )
    bots = domain_map.domain_for("/openapi/v1/bots/x")
    assert bots is not None
    assert bots.serves_http and not bots.serves_websocket
    assert bots.upstream_path("/openapi/v1/bots/x") == "/openapi/v1/bots/x"
