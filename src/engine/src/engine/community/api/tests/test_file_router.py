"""Phase 5 — HTTP contract tests for `engine/api/file/router.py`."""
from __future__ import annotations

import io
import asyncio
import sys
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


class TestCount:
    @pytest.mark.asyncio
    async def test_cancel_logs_failure_and_reaped_child_with_request_id(self, rich_manager, tmp_path, monkeypatch, caplog):
        from engine.community.api.file.router import count_files
        from engine.community.core.adapters.openclaw.file import OpenClawFileAdapter
        from engine.community.plugins.openclaw._file import _FilePortMixin
        from engine.community.plugins import file_count as scanner
        from engine.community.kernel.file_count import file_count_request_id

        work = tmp_path / "workspace"
        work.mkdir()
        monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", str(work))
        rich_manager._active_engine._file = OpenClawFileAdapter(_FilePortMixin())
        original_spawn = asyncio.create_subprocess_exec
        spawned = asyncio.Event()
        children = []

        async def slow(*args, **kwargs):
            process = await original_spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
            children.append(process)
            spawned.set()
            return process

        monkeypatch.setattr(scanner.asyncio, "create_subprocess_exec", slow)
        with caplog.at_level("INFO"):
            task = asyncio.create_task(count_files(path="workspace", request_id="cancel-correlated"))
            await spawned.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        cleanup = next(record for record in caplog.records if record.msg == "engine.file_count.cleanup")
        failure = next(record for record in caplog.records if record.msg == "engine.file_count.failure")
        assert cleanup.request_id == failure.fields["request_id"] == "cancel-correlated"
        assert cleanup.status == "reaped"
        assert failure.fields["status"] == "cancelled"
        assert children[0].returncode is not None
        assert file_count_request_id.get() == ""

    def test_real_openclaw_count(self, rich_manager, client, tmp_path, monkeypatch, caplog):
        from engine.community.core.adapters.openclaw.file import OpenClawFileAdapter
        from engine.community.plugins.openclaw._file import _FilePortMixin

        work = tmp_path / "workspace"
        work.mkdir()
        (work / "a").write_text("data")
        monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", str(work))
        rich_manager._active_engine._file = OpenClawFileAdapter(_FilePortMixin())
        with caplog.at_level("INFO", logger="api-file"):
            response = client.get("/api/file/count", params={"path": "workspace", "request_id": "req-1"})
        assert response.status_code == 200
        assert response.json()["data"]["file_count"] == 1
        assert response.json()["data"]["path"] == "workspace"
        events = [record for record in caplog.records if record.name == "api-file"]
        assert [record.msg for record in events] == ["engine.file_count.request", "engine.file_count.response"]
        assert all(record.fields["request_id"] == "req-1" for record in events)
        assert events[-1].fields["response"]["file_count"] == 1

    @pytest.mark.parametrize("code,status", [
        ("invalid_path", 400), ("not_directory", 400), ("path_not_found", 404),
        ("path_forbidden", 403), ("permission_denied", 403), ("scan_timeout", 408),
        ("directory_changed", 409), ("busy", 503), ("scan_failed", 500), ("unsupported", 501),
    ])
    def test_stable_errors(self, rich_manager, client, caplog, code, status):
        from engine.community.kernel.file_count import FileCountError

        plugin = MagicMock()
        plugin.count_files = AsyncMock(side_effect=FileCountError(code))
        rich_manager._active_engine._file = plugin
        with caplog.at_level("INFO", logger="api-file"):
            response = client.get("/api/file/count", params={"path": "workspace", "request_id": "err-1"})
        assert response.status_code == status
        assert response.json()["detail"] == code
        assert caplog.records[-1].fields["error_code"] == code

    def test_unexpected_error_never_leaks_credentials(self, rich_manager, client, caplog):
        plugin = MagicMock()
        plugin.count_files = AsyncMock(side_effect=RuntimeError("Authorization Bearer reusable-secret"))
        rich_manager._active_engine._file = plugin
        with caplog.at_level("INFO", logger="api-file"):
            response = client.get("/api/file/count", params={"path": "workspace", "request_id": "err-2"})
        assert response.status_code == 500
        assert response.json()["detail"] == "scan_failed"
        assert "reusable-secret" not in caplog.text + str([r.__dict__ for r in caplog.records])

    def test_unsupported(self, lean_manager, client):
        response = client.get("/api/file/count", params={"path": ".", "request_id": "unsupported"})
        assert response.status_code == 501
        assert response.json()["detail"] == "unsupported"


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
