"""HTTP contract tests for the aicoding ``/data/*`` proxy router."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.aicoding.data_proxy_router import router as data_proxy_router
from engine.community.core.data_proxy.protocol import (
    DataProxyError,
    ForwardResult,
    StreamingForwardResult,
    UpstreamUnreachable,
)
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.manager import EngineManager


class _FakeManager:
    engine = "fake"

    def __init__(self, active_engine, capabilities: EngineCapabilities) -> None:
        self._active_engine = active_engine
        self._capabilities = capabilities

    def get_capabilities(self) -> EngineCapabilities:
        return self._capabilities

    def _require_engine(self):
        return self._active_engine


class _EngineWithDataProxy:
    name = "fake"

    def __init__(self, data_proxy) -> None:
        self.data_proxy = data_proxy


class _EngineWithoutDataProxy:
    name = "fake"


async def _chunks():
    yield b"data: one\n\n"
    yield b"data: two\n\n"


@pytest.fixture(autouse=True)
def reset_manager():
    EngineManager.reset_instance()
    yield
    EngineManager.reset_instance()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(data_proxy_router)
    return TestClient(app)


def _install_manager(active_engine, *, supported: set[Capability]) -> None:
    EngineManager._instance = _FakeManager(
        active_engine,
        EngineCapabilities(supported=supported),
    )


def test_buffered_result_is_returned_as_response(client):
    plugin = AsyncMock()
    plugin.forward.return_value = ForwardResult(
        status_code=201,
        headers={"x-upstream": "yes"},
        content=b'{"ok": true}',
        media_type="application/json",
    )
    _install_manager(
        _EngineWithDataProxy(plugin),
        supported={Capability.DATA_PROXY_FORWARD},
    )

    resp = client.post(
        "/data/api/run?trace=1",
        content=b'{"input": 1}',
        headers={"x-token": "abc"},
    )

    assert resp.status_code == 201
    assert resp.headers["x-upstream"] == "yes"
    assert resp.content == b'{"ok": true}'
    plugin.forward.assert_awaited_once()
    forwarded = plugin.forward.await_args.kwargs
    assert forwarded["path"] == "api/run"
    assert forwarded["method"] == "POST"
    assert forwarded["query_string"] == "trace=1"
    assert forwarded["body"] == b'{"input": 1}'
    assert forwarded["headers"]["x-token"] == "abc"


def test_streaming_result_is_returned_as_streaming_response(client):
    plugin = AsyncMock()
    plugin.forward.return_value = StreamingForwardResult(
        status_code=200,
        headers={"cache-control": "no-cache"},
        body=_chunks(),
        media_type="text/event-stream",
    )
    _install_manager(
        _EngineWithDataProxy(plugin),
        supported={Capability.DATA_PROXY_FORWARD},
    )

    resp = client.get("/data/api/eval/stream")

    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.content == b"data: one\n\ndata: two\n\n"


def test_unsupported_engine_returns_501(client):
    plugin = AsyncMock()
    _install_manager(_EngineWithDataProxy(plugin), supported=set())

    resp = client.get("/data/api/run")

    assert resp.status_code == 501
    plugin.forward.assert_not_awaited()


def test_missing_data_proxy_plugin_returns_500(client):
    _install_manager(
        _EngineWithoutDataProxy(),
        supported={Capability.DATA_PROXY_FORWARD},
    )

    resp = client.get("/data/api/run")

    assert resp.status_code == 500
    assert "did not wire a data_proxy plugin" in resp.json()["detail"]["error"]


def test_upstream_unreachable_maps_to_502(client):
    plugin = AsyncMock()
    plugin.forward.side_effect = UpstreamUnreachable("harness-data down")
    _install_manager(
        _EngineWithDataProxy(plugin),
        supported={Capability.DATA_PROXY_FORWARD},
    )

    resp = client.get("/data/api/run")

    assert resp.status_code == 502
    assert resp.json()["detail"] == {"error": "harness-data down"}


def test_unknown_data_proxy_error_maps_to_500(client):
    class UnknownProxyError(DataProxyError):
        pass

    plugin = AsyncMock()
    plugin.forward.side_effect = UnknownProxyError("unexpected")
    _install_manager(
        _EngineWithDataProxy(plugin),
        supported={Capability.DATA_PROXY_FORWARD},
    )

    resp = client.get("/data/api/run")

    assert resp.status_code == 500
    assert resp.json()["detail"] == {"error": "unexpected"}
