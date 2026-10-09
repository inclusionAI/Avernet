"""Phase 2 — HTTP contract tests for `engine/api/mcp/router.py`.

Verifies every MCP endpoint dispatches through `manager.mcp` and that the
capability guards 501 when the active engine doesn't expose MCP.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.mcp.router import router as mcp_router
from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.registry import EngineRegistry
from engine.community.core.mcp.models import (
    MCPFilterResult,
    MCPServer,
    MCPServerConfig,
    MCPServerStatus,
    MCPToolCallRequest,
    MCPToolCallResult,
    TransportType,
)
from engine.community.manager import EngineManager


class _EngineWithMCP(BaseEngine):
    name = "rich"
    version = "1.0.0"

    _CAPABILITIES = EngineCapabilities(
        supported={
            Capability.MCP_LIST,
            Capability.MCP_CREATE,
            Capability.MCP_UPDATE,
            Capability.MCP_DELETE,
            Capability.MCP_START,
            Capability.MCP_STOP,
            Capability.MCP_FILTER_SERVERS,
            Capability.MCP_TOOLS_CALL,
        },
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock()
        self._chat = MagicMock()


class _EngineWithoutMCP(BaseEngine):
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


def _install_manager(engine_cls: type[BaseEngine]) -> EngineManager:
    EngineManager.reset_instance()
    registry = EngineRegistry()
    registry.register(engine_cls)
    m = EngineManager(engine_cls.name, registry=registry)
    m._active_engine = engine_cls()
    EngineManager._instance = m
    return m


@pytest.fixture
def rich_manager():
    m = _install_manager(_EngineWithMCP)
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def lean_manager():
    m = _install_manager(_EngineWithoutMCP)
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(mcp_router)
    return TestClient(app)


def _server(code: str = "fs") -> MCPServer:
    return MCPServer(
        config=MCPServerConfig(
            server_code=code,
            transport=TransportType.HTTP,
            url="http://localhost:9000",
            timeout_seconds=30,
            enabled=True,
        ),
        status=MCPServerStatus.RUNNING,
    )


class TestList:
    def test_dispatches(self, rich_manager, client):
        plugin = MagicMock()
        plugin.list_servers = AsyncMock(return_value=[_server("a"), _server("b")])
        rich_manager._active_engine._mcp = plugin

        resp = client.get("/api/mcp")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 2
        assert [s["server_code"] for s in body["data"]["servers"]] == ["a", "b"]
        plugin.list_servers.assert_awaited_once()

    def test_501(self, lean_manager, client):
        assert client.get("/api/mcp").status_code == 501


class TestGet:
    def test_found(self, rich_manager, client):
        plugin = MagicMock()
        plugin.get_server = AsyncMock(return_value=_server("fs"))
        rich_manager._active_engine._mcp = plugin

        resp = client.get("/api/mcp/fs")
        assert resp.status_code == 200
        assert resp.json()["data"]["server_code"] == "fs"

    def test_404(self, rich_manager, client):
        plugin = MagicMock()
        plugin.get_server = AsyncMock(return_value=None)
        rich_manager._active_engine._mcp = plugin

        assert client.get("/api/mcp/missing").status_code == 404


class TestCreate:
    def test_dispatches(self, rich_manager, client):
        plugin = MagicMock()
        plugin.create_server = AsyncMock(return_value=_server("new"))
        rich_manager._active_engine._mcp = plugin

        resp = client.post(
            "/api/mcp",
            json={
                "server_code": "new",
                "transport": "http",
                "url": "http://x",
                "timeout_seconds": 10,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["server_code"] == "new"
        plugin.create_server.assert_awaited_once()

    def test_409_when_exists(self, rich_manager, client):
        plugin = MagicMock()
        plugin.create_server = AsyncMock(side_effect=FileExistsError("dup"))
        rich_manager._active_engine._mcp = plugin

        resp = client.post(
            "/api/mcp", json={"server_code": "x", "transport": "sse", "url": "u"}
        )
        assert resp.status_code == 409


class TestUpdate:
    def test_404_when_missing(self, rich_manager, client):
        plugin = MagicMock()
        plugin.get_server = AsyncMock(return_value=None)
        rich_manager._active_engine._mcp = plugin

        resp = client.put("/api/mcp/missing", json={"enabled": False})
        assert resp.status_code == 404

    def test_dispatches_with_patch(self, rich_manager, client):
        plugin = MagicMock()
        plugin.get_server = AsyncMock(return_value=_server("fs"))
        plugin.update_server = AsyncMock(
            return_value=MCPServer(
                config=MCPServerConfig(
                    server_code="fs",
                    transport=TransportType.HTTP,
                    url="http://localhost:9000",
                    timeout_seconds=30,
                    enabled=False,
                ),
                status=MCPServerStatus.STOPPED,
            )
        )
        rich_manager._active_engine._mcp = plugin

        resp = client.put("/api/mcp/fs", json={"enabled": False})
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["enabled"] is False


class TestDelete:
    def test_dispatches(self, rich_manager, client):
        plugin = MagicMock()
        plugin.delete_server = AsyncMock(return_value=True)
        rich_manager._active_engine._mcp = plugin

        resp = client.delete("/api/mcp/fs")
        assert resp.status_code == 200

    def test_404(self, rich_manager, client):
        plugin = MagicMock()
        plugin.delete_server = AsyncMock(return_value=False)
        rich_manager._active_engine._mcp = plugin

        assert client.delete("/api/mcp/missing").status_code == 404


class TestFilterServers:
    def test_dispatches(self, rich_manager, client):
        plugin = MagicMock()
        plugin.filter_servers = AsyncMock(
            return_value=MCPFilterResult(
                server_codes=["a", "b"],
                command=["mcporter", "filter-servers", "a,b"],
                return_code=0,
                stdout="ok",
                stderr="",
            )
        )
        rich_manager._active_engine._mcp = plugin

        resp = client.post(
            "/api/mcp/filter-servers",
            json={"server_codes": ["a", "b"], "timeout_seconds": 5},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["server_codes"] == ["a", "b"]
        assert data["command"] == ["mcporter", "filter-servers", "a,b"]
        plugin.filter_servers.assert_awaited_once()

    def test_400_on_validation_error(self, rich_manager, client):
        plugin = MagicMock()
        plugin.filter_servers = AsyncMock(side_effect=ValueError("bad code"))
        rich_manager._active_engine._mcp = plugin

        resp = client.post("/api/mcp/filter-servers", json={"server_codes": ["a,b"]})
        assert resp.status_code == 400

    def test_501(self, lean_manager, client):
        resp = client.post("/api/mcp/filter-servers", json={"server_codes": []})
        assert resp.status_code == 501


class TestCallTool:
    def _result(self, *, server_code: str = "yuque-mcp", is_error: bool = False) -> MCPToolCallResult:
        return MCPToolCallResult(
            tool_name="skylark_resolve_url",
            server_code=server_code,
            content=[{"type": "text", "text": "resolved"}],
            is_error=is_error,
        )

    def test_server_code_defaults_to_none(self, rich_manager, client):
        """server_code is always None (engine auto-resolves via mcporter selector)."""
        plugin = MagicMock()
        plugin.call_tool = AsyncMock(return_value=self._result())
        rich_manager._active_engine._mcp = plugin

        resp = client.post(
            "/api/mcp/call-tool",
            json={"tool": "skylark_resolve_url", "args": []},
        )
        assert resp.status_code == 200
        sent: MCPToolCallRequest = plugin.call_tool.await_args.args[0]
        assert sent.server_code is None
        assert sent.arguments == {}

    def test_parses_multiple_args(self, rich_manager, client):
        """Only ``k=v`` args are parsed; bare tokens are ignored."""
        plugin = MagicMock()
        plugin.call_tool = AsyncMock(return_value=self._result())
        rich_manager._active_engine._mcp = plugin

        resp = client.post(
            "/api/mcp/call-tool",
            json={
                "tool": "t",
                "args": ["a=1", "b=x=y", "noequals"],
            },
        )
        assert resp.status_code == 200
        sent: MCPToolCallRequest = plugin.call_tool.await_args.args[0]
        assert sent.arguments == {"a": "1", "b": "x=y"}

    def test_error_result_sets_success_false(self, rich_manager, client):
        plugin = MagicMock()
        plugin.call_tool = AsyncMock(return_value=self._result(is_error=True))
        rich_manager._active_engine._mcp = plugin

        resp = client.post(
            "/api/mcp/call-tool",
            json={"tool": "t", "args": []},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["data"]["is_error"] is True

    def test_runtime_error_maps_to_502(self, rich_manager, client):
        plugin = MagicMock()
        plugin.call_tool = AsyncMock(side_effect=RuntimeError("relay down"))
        rich_manager._active_engine._mcp = plugin

        resp = client.post("/api/mcp/call-tool", json={"tool": "t", "args": []})
        assert resp.status_code == 502
        assert "relay down" in resp.json()["detail"]

    def test_unexpected_error_maps_to_500(self, rich_manager, client):
        plugin = MagicMock()
        plugin.call_tool = AsyncMock(side_effect=ValueError("boom"))
        rich_manager._active_engine._mcp = plugin

        resp = client.post("/api/mcp/call-tool", json={"tool": "t", "args": []})
        assert resp.status_code == 500
        assert "boom" in resp.json()["detail"]

    def test_501_when_engine_lacks_mcp(self, lean_manager, client):
        resp = client.post("/api/mcp/call-tool", json={"tool": "t", "args": []})
        assert resp.status_code == 501
