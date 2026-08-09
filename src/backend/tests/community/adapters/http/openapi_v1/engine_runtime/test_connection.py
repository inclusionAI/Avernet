"""Endpoint tests for the connection endpoint (Track C, Task 10)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from tests.community.adapters.http.openapi_v1.conftest import (
    user_scoped_client,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.connection import (
    router,
)
from agentclaw.community.api.engine_connection_service import (
    EngineConnectionServiceProtocol,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.errors import EngineDeviceNotReadyError
from agentclaw.community.core.engine_runtime.models import (
    ConnectionResult,
    EngineResult,
    SocketInfo,
)

from .conftest import BOT, OWNER, fails, ok

URL = f"/openapi/v1/bots/connection/{BOT}"


class FakeConnections:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.raises: Exception | None = None

    def __set_relay(self, relay):  # wired by the fixture
        self._relay = relay

    def build(self, *, bot_id, owner_id) -> ConnectionResult:
        # The real service resolves the caller's bot before touching the device
        # service; model that, or a foreign bot appears to succeed here.
        self._relay.resolve_bot(bot_id, owner_id)
        self.calls.append({"bot_id": bot_id, "owner_id": owner_id})
        if self.raises is not None:
            raise self.raises
        sockets = [
            SocketInfo(
                kind="chat",
                url=(
                    "wss://gw.example/openapi/v1/bots/messages/ws/tgt/api/openclaw/ws"
                    "?x-proxypass-token=tok"
                ),
            )
        ]
        return ConnectionResult(
            engine="openclaw", expires_at="2026-07-30T14:30:00+00:00", sockets=sockets
        )


@pytest.fixture
def connections(relay):
    fake = FakeConnections()
    fake._FakeConnections__set_relay(relay)
    return fake


@pytest.fixture
def client(relay, connections):
    class _M(Module):
        def configure(self, binder):
            binder.bind(EngineRuntimeRelayProtocol, to=relay)
            binder.bind(EngineConnectionServiceProtocol, to=connections)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": OWNER}
    attach_injector(app, Injector([_M()]))
    return user_scoped_client(app, OWNER)


def _caps(*supported: str) -> EngineResult:
    return EngineResult(data={"supported": list(supported)})


def test_chat_socket_is_offered(client, relay):
    data = ok(client.get(URL))
    assert [s["kind"] for s in data["sockets"]] == ["chat"]
    assert data["sockets"][0]["url"].startswith("wss://")
    assert data["engine"] == "openclaw"
    assert data["expires_at"]


def test_no_terminal_socket_is_offered(client, relay):
    """Removed deliberately — the spec excludes an interactive shell on a
    tenant's device from v1 at any scope."""
    assert [s["kind"] for s in ok(client.get(URL))["sockets"]] == ["chat"]


def test_no_capability_probe_is_needed(client, relay):
    """Chat is derived from the bot's active engine, a backend fact. Dropping
    the terminal socket also dropped a device call from this endpoint."""
    ok(client.get(URL))
    assert relay.calls == []


def test_payload_never_exposes_routing_internals(client, relay):
    """No target, type, or bare token field — that hand-off is what this
    endpoint exists to replace."""
    data = ok(client.get(URL))
    assert set(data) == {"engine", "expires_at", "sockets"}
    for socket in data["sockets"]:
        # ``url`` is the only field. The credential rides inside it — a browser
        # can carry one nowhere else on a handshake — and a second copy in a
        # ``headers`` field would leave a caller guessing which one is honoured.
        assert set(socket) == {"kind", "url"}
        assert "target" not in socket and "type" not in socket
        assert "token" not in socket


def test_connection_build_failure_is_enveloped(client, relay, connections):
    connections.raises = EngineDeviceNotReadyError("no binding")
    fails(client.get(URL), 409)


def test_foreign_bot_is_masked_404_without_building_a_connection(
    client, relay, connections
):
    assert fails(client.get("/openapi/v1/bots/connection/other"), 404)
    assert relay.calls == []
    assert connections.calls == []


def test_the_service_is_called_with_the_principals_owner_id(client, connections):
    """Never a caller-supplied identity: the wider permission model inside
    ``get_device_connection`` must not be reachable with someone else's id."""
    ok(client.get(URL))
    assert connections.calls[0] == {"bot_id": BOT, "owner_id": OWNER}
