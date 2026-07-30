"""Endpoint tests for the connection endpoint (Track C, Task 10)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

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

from .conftest import BOT, OWNER, FakeRelay, fails, ok

URL = f"/openapi/v1/bots/{BOT}/connection"


class FakeConnections:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.raises: Exception | None = None

    def build(self, *, bot_id, owner_id, include_terminal) -> ConnectionResult:
        self.calls.append(
            {"bot_id": bot_id, "owner_id": owner_id, "include_terminal": include_terminal}
        )
        if self.raises is not None:
            raise self.raises
        sockets = [
            SocketInfo(
                kind="chat",
                url="wss://gw.example/proxypass/tgt/api/openclaw/ws",
                headers={"x-proxypass-token": "tok"},
            )
        ]
        if include_terminal:
            sockets.append(
                SocketInfo(
                    kind="terminal",
                    url="wss://gw.example/proxypass/tgt/ws/terminal",
                    headers={"x-proxypass-token": "tok"},
                )
            )
        return ConnectionResult(
            engine="openclaw", expires_at="2026-07-30T14:30:00+00:00", sockets=sockets
        )


@pytest.fixture
def connections():
    return FakeConnections()


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
    return TestClient(app)


def _caps(*supported: str) -> EngineResult:
    return EngineResult(data={"supported": list(supported)})


def test_chat_socket_is_always_offered(client, relay):
    relay.results = [_caps("session.list")]
    data = ok(client.get(URL))
    assert [s["kind"] for s in data["sockets"]] == ["chat"]
    assert data["sockets"][0]["url"].startswith("wss://")
    assert data["engine"] == "openclaw"
    assert data["expires_at"]


def test_terminal_appears_only_when_the_engine_supports_it(client, relay, connections):
    relay.results = [_caps("web_shell.open")]
    data = ok(client.get(URL))
    assert [s["kind"] for s in data["sockets"]] == ["chat", "terminal"]
    assert connections.calls[0]["include_terminal"] is True


def test_terminal_absent_without_the_capability(client, relay, connections):
    relay.results = [_caps("session.list")]
    ok(client.get(URL))
    assert connections.calls[0]["include_terminal"] is False


def test_payload_never_exposes_routing_internals(client, relay):
    """No target, type, or bare token field — that hand-off is what this
    endpoint exists to replace."""
    relay.results = [_caps("web_shell.open")]
    data = ok(client.get(URL))
    assert set(data) == {"engine", "expires_at", "sockets"}
    for socket in data["sockets"]:
        assert set(socket) == {"kind", "url", "headers"}
        assert "target" not in socket and "type" not in socket
        assert "token" not in socket


def test_capabilities_failure_fails_the_endpoint(client, relay):
    """Rather than silently omitting a socket the bot may actually offer."""
    relay.raises = EngineDeviceNotReadyError("cold")
    assert fails(client.get(URL), 409)["message"] == "Bot device is not ready"


def test_connection_build_failure_is_enveloped(client, relay, connections):
    relay.results = [_caps()]
    connections.raises = EngineDeviceNotReadyError("no binding")
    fails(client.get(URL), 409)


def test_foreign_bot_is_masked_404_without_building_a_connection(
    client, relay, connections
):
    assert fails(client.get("/openapi/v1/bots/other/connection"), 404)
    assert relay.calls == []
    assert connections.calls == []


def test_the_service_is_called_with_the_principals_owner_id(client, relay, connections):
    """Never a caller-supplied identity: the wider permission model inside
    ``get_device_connection`` must not be reachable with someone else's id."""
    relay.results = [_caps()]
    ok(client.get(URL))
    assert connections.calls[0] == {
        "bot_id": BOT,
        "owner_id": OWNER,
        "include_terminal": False,
    }
