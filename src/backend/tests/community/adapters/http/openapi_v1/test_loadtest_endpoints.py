"""The load-test group answers a constant, echoes a socket, and gates both.

These two endpoints exist to be measured, so what is asserted here is exactly
what a load driver depends on: the HTTP one returns fixed bytes in the standard
envelope, the socket returns whatever it was sent, and **neither is reachable
without the gateway's signed principal**. That last one is the reason this file
drives real tokens through the real assembly rather than overriding
``require_principal``: a synthetic endpoint that quietly skipped the auth the
rest of the surface pays for would report a number no real caller can achieve,
and nothing about a passing "it returns hello world" test would say so.

The socket carries a second burden. A WebSocket route has no OpenAPI
representation, so ``test_path_convention.py`` — which reads the generated
document — cannot see it: the address ``/openapi/v1/bots/loadtest/ws/echo`` is
pinned here or nowhere, and the gateway's socket-plane claim will be written
against it.

Token minting is imported from ``test_principal_seam`` rather than repeated. It
is the same seam under test, and a second copy of the claim set would keep
passing here on the day the real one changes shape.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agentclaw.community.adapters.http.openapi_v1 import (
    PUBLIC_API_PREFIX,
    build_public_router,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    PRINCIPAL_HEADER,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.loadtest.router import HELLO_WORLD
from agentclaw.community.utils.gateway_principal_config import (
    reset_principal_verifier_config_cache,
)

from .conftest import mount_public_error_handlers
from .test_principal_seam import KEY, boot_with_key, mint

_HELLO = f"{PUBLIC_API_PREFIX}/bots/loadtest/hello"
_ECHO = f"{PUBLIC_API_PREFIX}/bots/loadtest/ws/echo"


@pytest.fixture(autouse=True)
def signing_key():
    """Install the shared verification key, and drop it again afterwards."""
    boot_with_key(KEY)
    yield
    reset_principal_verifier_config_cache()


@pytest.fixture
def client() -> TestClient:
    """The real public surface, with the app's own pre-handler error handlers.

    ``mount_public_error_handlers`` is what turns the auth seam's raise into the
    surface's 401 envelope — the dependency raises before any handler runs, so
    ``@envelope_errors`` never sees it.
    """
    app = FastAPI()
    app.include_router(build_public_router())
    return TestClient(mount_public_error_handlers(app))


def _authorized() -> dict[str, str]:
    return {PRINCIPAL_HEADER: mint()}


# ── the HTTP endpoint ───────────────────────────────────────────────────────


def test_hello_answers_the_constant_in_the_standard_envelope(client):
    """A load driver asserts on this body, so its shape is part of the contract."""
    response = client.get(_HELLO, headers=_authorized())

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200000
    assert body["message"] == "OK"
    assert body["data"] == {"message": HELLO_WORLD}


def test_hello_needs_no_user_id(client):
    """Not user-scoped, so the parameter every other operation requires is absent.

    Asserted as a *success without it* rather than as a schema property: the
    document side is covered by ``test_explicit_user_id.py``, and what matters
    to a driver is that the call it makes is complete.
    """
    assert client.get(_HELLO, headers=_authorized()).status_code == 200


def test_hello_refuses_a_caller_with_no_principal(client):
    """The whole point of measuring this path is that it includes the auth."""
    response = client.get(_HELLO)

    assert response.status_code == 401
    assert response.json()["data"] is None


def test_hello_refuses_a_principal_signed_with_another_key(client):
    """A forged token is refused exactly as an absent one is."""
    response = client.get(
        _HELLO, headers={PRINCIPAL_HEADER: mint(key="a-different-secret-key-32-bytes+")}
    )

    assert response.status_code == 401


# ── the socket ──────────────────────────────────────────────────────────────


def test_the_socket_echoes_a_text_frame(client):
    with client.websocket_connect(_ECHO, headers=_authorized()) as socket:
        socket.send_text("ping")

        assert socket.receive_text() == "ping"


def test_the_socket_echoes_a_binary_frame(client):
    """Both frame types, because a driver picks its own payload shape.

    This is what the raw ``receive()`` in the handler buys: ``receive_text``
    would answer a binary frame with a ``RuntimeError`` and take the choice away.
    """
    with client.websocket_connect(_ECHO, headers=_authorized()) as socket:
        socket.send_bytes(b"\x00\x01\x02")

        assert socket.receive_bytes() == b"\x00\x01\x02"


def test_the_socket_echoes_every_frame_in_order(client):
    """One connection, many frames — the shape a throughput run actually drives."""
    with client.websocket_connect(_ECHO, headers=_authorized()) as socket:
        for index in range(5):
            socket.send_text(f"frame-{index}")

        assert [socket.receive_text() for _ in range(5)] == [
            f"frame-{index}" for index in range(5)
        ]


def test_the_socket_returns_the_payload_byte_for_byte(client):
    """No trimming, no re-encoding, no interpretation of the payload."""
    payload = '  {"nested": "json"} \n\t☃  '

    with client.websocket_connect(_ECHO, headers=_authorized()) as socket:
        socket.send_text(payload)

        assert socket.receive_text() == payload


def test_the_socket_closes_cleanly_when_the_peer_goes_away(client):
    """Leaving the block disconnects; the handler must return, not raise.

    An exception escaping the endpoint would reach the ASGI error path on every
    single connection a load run closes, which is a lot of tracebacks for the
    normal end of a socket.
    """
    with client.websocket_connect(_ECHO, headers=_authorized()) as socket:
        socket.send_text("bye")
        assert socket.receive_text() == "bye"


def test_the_socket_refuses_a_handshake_with_no_principal(client):
    """1008, and never accepted — the socket plane's form of the 401.

    A handshake has no body to put an envelope in, so the refusal is the close
    code. It happens in the dependency, before the endpoint runs, so a rejected
    caller never gets an open connection at all.
    """
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect(_ECHO):
            pass

    assert refused.value.code == 1008


def test_the_socket_refuses_a_principal_signed_with_another_key(client):
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect(
            _ECHO,
            headers={PRINCIPAL_HEADER: mint(key="a-different-secret-key-32-bytes+")},
        ):
            pass

    assert refused.value.code == 1008


# ── the address and the guard, asserted where the document cannot ───────────


def _routes():
    """Every route of the assembled public surface, flattened.

    ``include_router`` stores a lazy wrapper rather than copying routes, so the
    nesting has to be walked — the same shape ``test_principal_seam`` uses.
    """
    found = []

    def walk(router):
        for route in getattr(router, "routes", []):
            if hasattr(route, "dependant"):
                found.append(route)
            elif hasattr(route, "original_router"):
                walk(route.original_router)
            else:
                walk(route)

    walk(build_public_router())
    return found


def _loadtest_routes():
    return [route for route in _routes() if "/loadtest" in route.path]


def test_the_group_publishes_exactly_the_two_endpoints():
    """A third one added here would be a third thing every run has to explain."""
    assert sorted(route.path for route in _loadtest_routes()) == [_HELLO, _ECHO]


def test_the_socket_address_is_pinned_here_or_nowhere():
    """The generated document has no WebSocket, so this is the only guard.

    The ``ws`` segment is what the gateway's socket-plane claim gets pinned to
    (``/openapi/v1/bots/loadtest/ws/**``), so moving the endpoint out of that
    subtree makes it unroutable through the gateway with nothing failing here.
    """
    sockets = [route for route in _loadtest_routes() if not hasattr(route, "methods")]

    assert [route.path for route in sockets] == [_ECHO]


def test_both_endpoints_are_gated_by_require_principal():
    """Declared per route, so the route's own dependant carries the guard.

    ``test_public_routes_require_principal`` makes this assertion for the whole
    surface; it is repeated here because the socket is the first route on it
    that the *group-level* declaration alone would have to cover, and this file
    is where a change to the group's wiring is read.
    """
    def gated(dependant) -> bool:
        if dependant.call is require_principal:
            return True
        return any(gated(sub) for sub in dependant.dependencies)

    ungated = [route.path for route in _loadtest_routes() if not gated(route.dependant)]

    assert not ungated, f"load-test routes not gated by require_principal: {ungated}"
