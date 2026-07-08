"""Phase 4 — HTTP contract tests for `engine/api/node/router.py`.

Verifies `/api/nodes` flows through `manager.node` and 501s when the
active engine doesn't declare ``NODE_LIST``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.node.router import router as node_router
from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.registry import EngineRegistry
from engine.community.core.node.models import Node
from engine.community.manager import EngineManager


class _EngineWithNode(BaseEngine):
    name = "rich"
    version = "1.0.0"
    _CAPABILITIES = EngineCapabilities(
        supported={Capability.NODE_LIST, Capability.NODE_REGISTER, Capability.NODE_STATUS},
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock()
        self._chat = MagicMock()


class _EngineWithoutNode(BaseEngine):
    name = "lean"
    version = "0.1.0"
    _CAPABILITIES = EngineCapabilities(
        supported={Capability.SESSION_LIST, Capability.CHAT_STREAM},
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock()
        self._chat = MagicMock()


def _install(engine_cls: type[BaseEngine]) -> EngineManager:
    EngineManager.reset_instance()
    registry = EngineRegistry()
    registry.register(engine_cls)
    m = EngineManager(engine_cls.name, registry=registry)
    m._active_engine = engine_cls()
    EngineManager._instance = m
    return m


@pytest.fixture
def rich_manager():
    m = _install(_EngineWithNode)
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def lean_manager():
    m = _install(_EngineWithoutNode)
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(node_router)
    return TestClient(app)


def test_dispatches_with_filters(rich_manager, client):
    plugin = MagicMock()
    plugin.list_nodes = AsyncMock(
        return_value=[
            Node(nodeId="n1", platform="macos", status="online"),
            Node(nodeId="n2", platform="linux", status="paired"),
        ]
    )
    rich_manager._active_engine._node = plugin

    resp = client.get("/api/nodes?status=online&platform=macos&limit=10&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 2  # plugin returned both; filtering would happen in plugin
    assert body["data"][0]["nodeId"] == "n1"

    plugin.list_nodes.assert_awaited_once()
    req = plugin.list_nodes.await_args.args[0]
    assert req.status == "online"
    assert req.platform == "macos"
    assert req.limit == 10


def test_unsupported_engine_501(lean_manager, client):
    assert client.get("/api/nodes").status_code == 501


def test_returns_camelcase_dict(rich_manager, client):
    plugin = MagicMock()
    plugin.list_nodes = AsyncMock(
        return_value=[
            Node(
                nodeId="x",
                displayName="Box",
                platform="macos",
                version="1.0",
                capabilities=["a"],
                commands=["b"],
                remoteIp="1.2.3.4",
                status="online",
            ),
        ]
    )
    rich_manager._active_engine._node = plugin

    body = client.get("/api/nodes").json()
    n = body["data"][0]
    assert n["nodeId"] == "x"
    assert n["displayName"] == "Box"
    assert n["remoteIp"] == "1.2.3.4"
    assert n["capabilities"] == ["a"]
