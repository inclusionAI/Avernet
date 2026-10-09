"""
Unit tests for the neutral openclaw HTTP router.

Originally these covered the corp ``openclaw/router.py`` HTTP endpoints
(``/test-connection``, ``/disconnect``, ``/config``). After the
unified-mount refactor those endpoints moved to the neutral
``engine.community.api.routers.openclaw_http`` router, with deps injected via the
``OpenClawGatewayService`` Protocol. The tests now exercise the neutral router
with a fake service bound through the injector — preserving the original
response-shape coverage while keeping the test corp-transport-free.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Binder, Injector, InstanceProvider, Module, singleton

from engine.community.api.routers.openclaw_http import router
from engine.community.plugin_api.openclaw.gateway_service import OpenClawGatewayService
from engine.community.plugin_api.auth_gate.protocol import AuthGateService


class FakeAuthGateService:
    async def verify(self, token: str, content: str, session_id: str):  # pragma: no cover
        raise NotImplementedError

    async def get_switch(self) -> bool:  # pragma: no cover
        return True

    async def set_switch(self, enabled: bool) -> None:  # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_service():
    """Fresh fake gateway service per test; tests set return values as needed."""
    return MagicMock(spec=OpenClawGatewayService)


@pytest.fixture()
def app(fake_service):
    _app = FastAPI()

    class _Module(Module):
        def configure(self, binder: Binder) -> None:
            binder.bind(
                OpenClawGatewayService,
                to=InstanceProvider(fake_service),
                scope=singleton,
            )
            binder.bind(
                AuthGateService,
                to=InstanceProvider(FakeAuthGateService()),
                scope=singleton,
            )

    attach_injector(_app, Injector([_Module()]))
    _app.include_router(router)
    return _app


# ---------------------------------------------------------------------------
# /api/openclaw/config
# ---------------------------------------------------------------------------

class TestGetOpenClawConfig:
    def test_returns_config(self, app, fake_service):
        fake_service.get_config.return_value = {
            "gateway_url": "ws://openclaw-test",
            "connection_timeout": 10,
        }
        with TestClient(app) as client:
            resp = client.get("/api/openclaw/config")

        assert resp.status_code == 200
        body = resp.json()
        assert body["gateway_url"] == "ws://openclaw-test"
        assert body["connection_timeout"] == 10


# ---------------------------------------------------------------------------
# /api/openclaw/test-connection  (success paths)
# ---------------------------------------------------------------------------

class TestTestConnectionSuccess:
    def _make_hello_body(self, version="2.0", conn_id="oc-cid", host="oc-host", protocol=3):
        return {
            "success": True,
            "connected": True,
            "gateway_url": "ws://openclaw-test",
            "server": {
                "version": version,
                "conn_id": conn_id,
                "host": host,
            },
            "protocol": protocol,
            "features": {
                "methods": ["connect", "node.list"],
                "events": ["tick", "agent"],
            },
        }

    def test_connected_with_hello(self, app, fake_service):
        body = self._make_hello_body()

        async def _ret():
            return body

        fake_service.test_connection = MagicMock(return_value=_ret())
        with TestClient(app) as client:
            resp = client.get("/api/openclaw/test-connection")

        assert resp.status_code == 200
        rb = resp.json()
        assert rb["success"] is True
        assert rb["connected"] is True
        assert rb["gateway_url"] == "ws://openclaw-test"
        assert rb["server"]["version"] == "2.0"
        assert rb["server"]["conn_id"] == "oc-cid"
        assert rb["protocol"] == 3
        assert "node.list" in rb["features"]["methods"]

    def test_connected_without_hello_returns_none_fields(self, app, fake_service):
        body = {
            "success": True,
            "connected": True,
            "gateway_url": "ws://openclaw-test",
            "server": None,
            "protocol": None,
            "features": None,
        }

        async def _ret():
            return body

        fake_service.test_connection = MagicMock(return_value=_ret())
        with TestClient(app) as client:
            resp = client.get("/api/openclaw/test-connection")

        rb = resp.json()
        assert rb["success"] is True
        assert rb["server"] is None
        assert rb["protocol"] is None
        assert rb["features"] is None

    def test_not_connected_still_success_true(self, app, fake_service):
        """Endpoint returns success=True as long as the service doesn't raise."""
        body = {
            "success": True,
            "connected": False,
            "gateway_url": "ws://openclaw-test",
            "server": None,
            "protocol": None,
            "features": None,
        }

        async def _ret():
            return body

        fake_service.test_connection = MagicMock(return_value=_ret())
        with TestClient(app) as client:
            resp = client.get("/api/openclaw/test-connection")

        rb = resp.json()
        assert rb["success"] is True
        assert rb["connected"] is False


# ---------------------------------------------------------------------------
# /api/openclaw/test-connection  (error path)
# ---------------------------------------------------------------------------

class TestTestConnectionError:
    def test_exception_returns_failure_body(self, app, fake_service):
        body = {
            "success": False,
            "connected": False,
            "gateway_url": "ws://openclaw-test",
            "error": "port closed",
        }

        async def _ret():
            return body

        fake_service.test_connection = MagicMock(return_value=_ret())
        with TestClient(app) as client:
            resp = client.get("/api/openclaw/test-connection")

        assert resp.status_code == 200
        rb = resp.json()
        assert rb["success"] is False
        assert rb["connected"] is False
        assert "port closed" in rb["error"]

    def test_generic_exception_caught(self, app, fake_service):
        body = {
            "success": False,
            "connected": False,
            "gateway_url": "ws://openclaw-test",
            "error": "unexpected",
        }

        async def _ret():
            return body

        fake_service.test_connection = MagicMock(return_value=_ret())
        with TestClient(app) as client:
            resp = client.get("/api/openclaw/test-connection")

        rb = resp.json()
        assert rb["success"] is False
        assert "unexpected" in rb["error"]


# ---------------------------------------------------------------------------
# /api/openclaw/disconnect
# ---------------------------------------------------------------------------

class TestDisconnect:
    def test_success(self, app, fake_service):
        async def _ret():
            return None

        fake_service.disconnect = MagicMock(return_value=_ret())
        with TestClient(app) as client:
            resp = client.post("/api/openclaw/disconnect")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "Disconnected" in body["message"]

    def test_exception_raises_http_500(self, app, fake_service):
        async def _ret():
            raise RuntimeError("close failed")

        fake_service.disconnect = MagicMock(return_value=_ret())
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/api/openclaw/disconnect")

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# /api/openclaw/ws endpoint-parameter injected AuthGateService
#
# Note: the /api/openclaw/ws endpoint was hoisted to the neutral /ws router
# (engine.community.api.routers.ws). Its coverage now lives alongside that router.
# The /client ws_proxy remains corp-only and is covered separately if needed.
# ---------------------------------------------------------------------------
