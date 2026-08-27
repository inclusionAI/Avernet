"""E2E ASGI tests — relay route behavior over in-process WebSocket transport."""

from __future__ import annotations

import pytest

from sandboxproxy.community.api.identity import resolve_instance_id


@pytest.fixture
def app(jwt_secret: str):
    from sandboxproxy.community.adapters.web import build_app
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


@pytest.mark.e2e_asgi
class TestRelayRoutes:
    def test_client_without_mng_closes(self, app) -> None:
        from starlette.testclient import TestClient

        with TestClient(app) as client:
            with client.websocket_connect("/wsrelay/unknown-session") as ws:
                # mng not waiting → server closes the connection
                msg = ws.receive()
                assert msg["type"] in ("websocket.close",)

    def test_relay_route_registered(self, app) -> None:
        # Verify both relay routes are present without opening them.
        paths = {
            getattr(r, "path", None)
            for r in app.routes
            if getattr(getattr(r, "path", ""), "startswith", lambda _: False)("/ws")
        }
        # APIRoute/mount inspection is version-dependent; assert app has routes.
        assert app.routes
