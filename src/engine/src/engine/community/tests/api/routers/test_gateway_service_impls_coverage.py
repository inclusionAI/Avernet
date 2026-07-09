"""Cover the shared OpenClawGatewayService implementation and profile DI bindings."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from engine.community.openclaw.gateway_service_impl import OpenClawGatewayServiceImpl


@pytest.mark.asyncio
async def test_get_config_shape(monkeypatch):
    fake_cfg = MagicMock()
    fake_cfg.gateway_url = "ws://127.0.0.1:18789"
    fake_cfg.connection_timeout = 10
    monkeypatch.setattr("engine.community.openclaw.gateway_service_impl.get_config", lambda: fake_cfg)
    svc = OpenClawGatewayServiceImpl()
    cfg = svc.get_config()
    assert cfg == {"gateway_url": "ws://127.0.0.1:18789", "connection_timeout": 10}


@pytest.mark.asyncio
async def test_test_connection_error_path(monkeypatch):
    async def boom():
        raise RuntimeError("no gateway")

    monkeypatch.setattr("engine.community.openclaw.gateway_service_impl.get_client", boom)
    fake_cfg = MagicMock()
    fake_cfg.gateway_url = "ws://x"
    monkeypatch.setattr("engine.community.openclaw.gateway_service_impl.get_config", lambda: fake_cfg)
    svc = OpenClawGatewayServiceImpl()
    r = await svc.test_connection()
    assert r["success"] is False
    assert r["connected"] is False
    assert r["gateway_url"] == "ws://x"
    assert "no gateway" in r["error"]


@pytest.mark.asyncio
async def test_test_connection_success_path(monkeypatch):
    hello = MagicMock()
    hello.server.version = "1.2"
    hello.server.conn_id = "c1"
    hello.server.host = "h"
    hello.protocol = 3
    hello.features.methods = ["chat.send"]
    hello.features.events = ["chat.delta"]
    client = MagicMock()
    client.hello = hello
    client.connected = True

    async def fake_get():
        return client

    monkeypatch.setattr("engine.community.openclaw.gateway_service_impl.get_client", fake_get)
    fake_cfg = MagicMock()
    fake_cfg.gateway_url = "ws://y"
    monkeypatch.setattr("engine.community.openclaw.gateway_service_impl.get_config", lambda: fake_cfg)
    svc = OpenClawGatewayServiceImpl()
    r = await svc.test_connection()
    assert r["success"] is True
    assert r["connected"] is True
    assert r["gateway_url"] == "ws://y"
    assert r["server"]["version"] == "1.2"
    assert r["protocol"] == 3
    assert r["features"]["methods"] == ["chat.send"]


@pytest.mark.asyncio
async def test_test_connection_no_hello(monkeypatch):
    client = MagicMock()
    client.hello = None
    client.connected = False

    async def fake_get():
        return client

    monkeypatch.setattr("engine.community.openclaw.gateway_service_impl.get_client", fake_get)
    fake_cfg = MagicMock()
    fake_cfg.gateway_url = "ws://z"
    monkeypatch.setattr("engine.community.openclaw.gateway_service_impl.get_config", lambda: fake_cfg)
    svc = OpenClawGatewayServiceImpl()
    r = await svc.test_connection()
    assert r["success"] is True
    assert r["connected"] is False
    assert r["server"] is None
    assert r["protocol"] is None
    assert r["features"] is None


@pytest.mark.asyncio
async def test_disconnect(monkeypatch):
    called = {}

    async def fake_close():
        called["closed"] = True

    monkeypatch.setattr("engine.community.openclaw.gateway_service_impl.close_client", fake_close)
    svc = OpenClawGatewayServiceImpl()
    await svc.disconnect()
    assert called.get("closed") is True


def test_di_module_resolves_shared_impl(monkeypatch):
    from engine.community.di.modules.infrastructure.shared.openclaw_gateway import OpenClawGatewayModule

    assert isinstance(OpenClawGatewayModule().openclaw_gateway(), OpenClawGatewayServiceImpl)
