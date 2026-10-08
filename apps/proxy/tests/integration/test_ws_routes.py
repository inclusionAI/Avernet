"""Integration tests — WebSocket proxypass + relay routes via in-process ASGI."""

from __future__ import annotations

import asyncio
import os
import time

import pytest
import websockets.asyncio.client
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close

_SECRET = "ws-secret"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOXPROXY_JWT_SECRET", _SECRET)
    cfg = tmp_path / "application.yaml"
    cfg.write_text(
        "app_name: sandboxproxy\n"
        "user_config:\n"
        "  plugins:\n"
        "    resolver: stub\n"
        "    relay_client: stub\n"
        "  jwt:\n"
        f"    secret: {_SECRET}\n"
    )
    monkeypatch.setenv("SANDBOXPROXY_CONFIG_PATH", str(cfg))

    from sandboxproxy.community.adapters.web import build_app
    from sandboxproxy.community.api.identity import resolve_instance_id
    from sandboxproxy.community.bootstrap import (
        ApplicationContainer,
        initialize_services,
    )
    from sandboxproxy.community.config import ConfigLoader

    loaded = ConfigLoader.load()
    container = ApplicationContainer()
    container.config.from_dict(
        {
            "user_config": loaded.user_config.model_dump(),
            "plugins": {"resolver": "stub", "relay_client": "stub"},
            "instance": resolve_instance_id(),
        }
    )
    initialize_services(container)
    return build_app(container, loaded)


@pytest.mark.integration
class TestWsRoutes:
    def test_proxypass_ws_no_auth_closes(self, app) -> None:
        from starlette.testclient import TestClient

        with TestClient(app) as client:
            # proxypass websocket route accepts then closes on invalid target
            with client.websocket_connect("/proxypass/BADTARGET") as ws:
                msg = ws.receive()
                assert msg["type"] in ("websocket.close", "websocket.send")

    def test_wsrelay_closes_without_mng(self, app) -> None:
        from starlette.testclient import TestClient

        with TestClient(app) as client:
            with client.websocket_connect("/wsrelay/no-mng") as ws:
                msg = ws.receive()
                assert msg["type"] == "websocket.close"

    def test_wsrevrelay_registers_and_waits(self, app) -> None:
        from starlette.testclient import TestClient

        with TestClient(app) as client:
            with client.websocket_connect("/wsrevrelay/sess-x") as ws:
                # mng registers; no client yet → stays open waiting
                ws.send_text("hello")


class _FakeUpstreamWS:
    """Mimics the upstream ClientConnection contract used by routes._bridge.

    ``script`` items are returned by ``recv()`` in order; a ``BaseException``
    item is raised, a ``str``/``bytes`` item is a data frame. Once the script is
    exhausted ``recv()`` blocks forever (until the task is cancelled), which is
    what a quiet upstream looks like.
    """

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.received: list = []
        self.exited = False

    async def recv(self):
        if self._script:
            item = self._script.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        await asyncio.Event().wait()

    async def send(self, data) -> None:
        self.received.append(data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> bool:
        self.exited = True
        return False


class _SendFailsUpstreamWS(_FakeUpstreamWS):
    """Upstream whose send() raises as if the tunnel already closed."""

    async def send(self, data) -> None:
        raise ConnectionClosed(None, None)


def _patch_upstream_connect(monkeypatch, script: list, factory=_FakeUpstreamWS):
    """Replace websockets.connect with a factory producing scripted fakes."""
    created = []

    def connect(url: str, **kwargs):
        conn = factory(script)
        created.append((url, kwargs, conn))
        return conn

    monkeypatch.setattr(websockets.asyncio.client, "connect", connect)
    return created


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.mark.integration
class TestProxypassWsTunnel:
    """Frame-level behaviour of the proxypass websocket tunnel (_bridge)."""

    def test_upstream_frames_forwarded_and_close_code_propagated(
        self, app, monkeypatch
    ) -> None:
        closed = ConnectionClosed(Close(1011, "upstream gone"), None)
        created = _patch_upstream_connect(monkeypatch, ["up-text", b"up-bin", closed])
        with TestClient(app) as client:
            with client.websocket_connect("/proxypass/STUB/sock") as ws:
                assert ws.receive_text() == "up-text"
                assert ws.receive_bytes() == b"up-bin"
                with pytest.raises(WebSocketDisconnect) as exc_info:
                    ws.receive_text()
                assert exc_info.value.code == 1011
        url, _, conn = created[0]
        assert url == "wss://127.0.0.1:9999/sock"
        assert _wait_until(lambda: conn.exited), "upstream ws was not exited"

    def test_client_text_and_binary_frames_forwarded_upstream(
        self, app, monkeypatch
    ) -> None:
        created = _patch_upstream_connect(monkeypatch, [])
        with TestClient(app) as client:
            with client.websocket_connect("/proxypass/STUB/sock") as ws:
                ws.send_text("hello")
                ws.send_bytes(b"binary")
        _, _, conn = created[0]
        assert _wait_until(lambda: conn.exited), "upstream ws was not exited"
        assert conn.received == ["hello", b"binary"]

    def test_upstream_close_code_without_reason_still_propagated(
        self, app, monkeypatch
    ) -> None:
        # rcvd=None → tunnel falls back to code 1011
        created = _patch_upstream_connect(monkeypatch, [ConnectionClosed(None, None)])
        with TestClient(app) as client:
            with client.websocket_connect("/proxypass/STUB/sock") as ws:
                with pytest.raises(WebSocketDisconnect) as exc_info:
                    ws.receive_text()
                assert exc_info.value.code == 1011
        _, _, conn = created[0]
        assert _wait_until(lambda: conn.exited), "upstream ws was not exited"

    def test_upstream_recv_runtime_error_is_ignored(self, app, monkeypatch) -> None:
        created = _patch_upstream_connect(monkeypatch, [RuntimeError("boom")])
        with TestClient(app) as client:
            with client.websocket_connect("/proxypass/STUB/sock") as ws:
                ws.send_text("ignored")
        _, _, conn = created[0]
        assert _wait_until(lambda: conn.exited), "upstream ws was not exited"

    def test_upstream_send_connection_closed_stops_tunnel(
        self, app, monkeypatch
    ) -> None:
        created = _patch_upstream_connect(monkeypatch, [], factory=_SendFailsUpstreamWS)
        with TestClient(app) as client:
            with client.websocket_connect("/proxypass/STUB/sock") as ws:
                ws.send_text("hello")
        _, _, conn = created[0]
        assert _wait_until(lambda: conn.exited), "upstream ws was not exited"


class _TornDownClientWS:
    """Client websocket whose close() fails, e.g. client already disconnected.

    Only the to_client side of _bridge matters here: to_upstream parks on
    receive() forever and ends up cancelled.
    """

    def __init__(self) -> None:
        self.close_codes: list[int] = []

    async def receive(self) -> dict:
        await asyncio.Event().wait()

    async def send_text(self, data: str) -> None:
        raise AssertionError("no downstream frames in this scenario")

    async def send_bytes(self, data: bytes) -> None:
        raise AssertionError("no downstream frames in this scenario")

    async def close(self, code: int = 1000, reason=None) -> None:
        self.close_codes.append(code)
        raise RuntimeError('Cannot call "send" once a close message has been sent.')


async def test_bridge_swallow_close_runtime_error() -> None:
    """websocket.close() failing after disconnect must not bubble out."""
    from sandboxproxy.community.adapters.web.routes import _bridge

    client_ws = _TornDownClientWS()
    upstream = _FakeUpstreamWS([ConnectionClosed(Close(1010, "stale"), None)])

    await _bridge(client_ws, upstream)

    assert client_ws.close_codes == [1010]


class TestHelpers:
    def test_to_ws_url(self) -> None:
        from sandboxproxy.community.adapters.web.routes import _to_ws_url

        assert _to_ws_url("http://x:1", "/p") == "ws://x:1/p"
        assert _to_ws_url("https://x:1", "/p") == "wss://x:1/p"

    def test_extra_headers(self) -> None:
        from sandboxproxy.community.adapters.web.routes import _extra_headers

        assert _extra_headers({"x-target-bot-id": "b1"}) == {"x-target-bot-id": "b1"}
        assert _extra_headers({"local_path_prefix": "/p"}) == {
            "x-local-path-prefix": "/p"
        }

    def test_upstream_url(self) -> None:
        from sandboxproxy.community.adapters.web.routes import _upstream_url

        assert _upstream_url({"arca_host": "h1"}) == "https://h1"
        assert _upstream_url({"arca_host": "http://h1"}) == "http://h1"
        assert _upstream_url({"pod_ip": "10.0.0.7", "pod_port": "20003"}) == (
            "http://10.0.0.7:20003"
        )
