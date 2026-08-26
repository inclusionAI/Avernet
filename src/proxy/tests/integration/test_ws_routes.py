"""Integration tests — WebSocket proxypass + relay routes via in-process ASGI."""

from __future__ import annotations

import os

import pytest

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
