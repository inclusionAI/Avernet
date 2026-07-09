"""Phase 5 — HTTP contract tests for `engine/api/file/router.py`."""
from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.file.router import router as file_router
from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.registry import EngineRegistry
from engine.community.core.file.models import (
    FileEntry,
    ListDirResult,
    RemoveResult,
    UploadResult,
)
from engine.community.manager import EngineManager


class _EngineWithFile(BaseEngine):
    name = "rich"
    version = "1.0.0"
    _CAPABILITIES = EngineCapabilities(
        supported={
            Capability.FILE_READ,
            Capability.FILE_WRITE,
            Capability.FILE_UPLOAD,
            Capability.FILE_DELETE,
            Capability.FILE_LIST,
        },
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock()
        self._chat = MagicMock()


class _EngineWithoutFile(BaseEngine):
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
    m = _install(_EngineWithFile)
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def lean_manager():
    m = _install(_EngineWithoutFile)
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(file_router)
    return TestClient(app)


class TestUpload:
    def test_dispatches(self, rich_manager, client):
        plugin = MagicMock()
        plugin.upload = AsyncMock(
            return_value=UploadResult(
                target_path="/x/y", size=4, overwritten=False,
            )
        )
        rich_manager._active_engine._file = plugin

        resp = client.post(
            "/api/file/upload",
            data={"target_path": "/x/y"},
            files={"file": ("hello.txt", io.BytesIO(b"data"), "text/plain")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["target_path"] == "/x/y"
        assert body["data"]["overwritten"] is False
        plugin.upload.assert_awaited_once()

    def test_404_passthrough(self, rich_manager, client):
        plugin = MagicMock()
        plugin.upload = AsyncMock(side_effect=IsADirectoryError("dir blocks"))
        rich_manager._active_engine._file = plugin

        resp = client.post(
            "/api/file/upload",
            data={"target_path": "/x/y"},
            files={"file": ("x.txt", io.BytesIO(b"a"), "text/plain")},
        )
        assert resp.status_code == 409

    def test_501(self, lean_manager, client):
        resp = client.post(
            "/api/file/upload",
            data={"target_path": "/x"},
            files={"file": ("x.txt", io.BytesIO(b"a"), "text/plain")},
        )
        assert resp.status_code == 501


class TestRead:
    def test_returns_streamed_bytes(self, rich_manager, client):
        plugin = MagicMock()
        plugin.read = AsyncMock(return_value=b"file content")
        rich_manager._active_engine._file = plugin

        resp = client.post("/api/file/read", json={"file_path": "/x"})
        assert resp.status_code == 200
        assert resp.content == b"file content"

    def test_404(self, rich_manager, client):
        plugin = MagicMock()
        plugin.read = AsyncMock(side_effect=FileNotFoundError("nope"))
        rich_manager._active_engine._file = plugin

        resp = client.post("/api/file/read", json={"file_path": "/x"})
        assert resp.status_code == 404


class TestRemove:
    def test_dispatches(self, rich_manager, client):
        plugin = MagicMock()
        plugin.remove = AsyncMock(
            return_value=RemoveResult(target_path="/x", path_type="file")
        )
        rich_manager._active_engine._file = plugin

        resp = client.post("/api/file/remove", json={"target_path": "/x"})
        assert resp.status_code == 200
        assert resp.json()["data"]["path_type"] == "file"

    def test_404(self, rich_manager, client):
        plugin = MagicMock()
        plugin.remove = AsyncMock(side_effect=FileNotFoundError("missing"))
        rich_manager._active_engine._file = plugin

        resp = client.post("/api/file/remove", json={"target_path": "/x"})
        assert resp.status_code == 404


class TestRmtree:
    def test_dispatches(self, rich_manager, client):
        plugin = MagicMock()
        plugin.rmtree = AsyncMock(return_value="/x/y")
        rich_manager._active_engine._file = plugin

        resp = client.post("/api/file/rmtree", json={"target_path": "/x/y"})
        assert resp.status_code == 200
        assert resp.json()["data"]["target_path"] == "/x/y"

    def test_400_when_not_a_dir(self, rich_manager, client):
        plugin = MagicMock()
        plugin.rmtree = AsyncMock(side_effect=NotADirectoryError("not dir"))
        rich_manager._active_engine._file = plugin

        resp = client.post("/api/file/rmtree", json={"target_path": "/x"})
        assert resp.status_code == 400


class TestListDir:
    def test_dispatches(self, rich_manager, client):
        plugin = MagicMock()
        plugin.list_dir = AsyncMock(
            return_value=ListDirResult(
                dir_path="/x",
                recursive=False,
                files=[
                    FileEntry(
                        name="a.txt", path="/x/a.txt",
                        relative_path="a.txt", is_dir=False, size=10,
                    ),
                ],
            )
        )
        rich_manager._active_engine._file = plugin

        resp = client.post("/api/file/list", json={"dir_path": "/x"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 1
        assert body["data"]["files"][0]["name"] == "a.txt"
        assert body["data"]["files"][0]["size"] == 10

    def test_501(self, lean_manager, client):
        resp = client.post("/api/file/list", json={"dir_path": "/x"})
        assert resp.status_code == 501
