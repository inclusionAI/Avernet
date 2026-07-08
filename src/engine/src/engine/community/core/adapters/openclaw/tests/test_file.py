"""Unit tests for the OpenClaw file ACL adapter.

Drives ``OpenClawFileAdapter`` against a fake ``OpenClawFilePort`` that
returns canned primitive dicts/bytes.  Verifies DTO construction and that
exceptions raised by the port propagate unchanged (the router handles them).
"""
from __future__ import annotations

import pytest

from engine.community.core.adapters.openclaw.file import OpenClawFileAdapter
from engine.community.core.file.models import FileEntry, ListDirResult, RemoveResult, UploadResult


class _FakeFilePort:
    """Minimal fake that returns canned results or raises on demand."""

    def __init__(self) -> None:
        self._upload_result: dict | None = None
        self._read_result: bytes = b""
        self._remove_result: dict | None = None
        self._rmtree_result: str = ""
        self._list_dir_result: dict | None = None
        self._raise: Exception | None = None

    # ── setup helpers ──

    def will_upload(self, path: str, size: int, overwritten: bool) -> None:
        self._upload_result = {
            "target_path": path,
            "size": size,
            "overwritten": overwritten,
        }

    def will_read(self, data: bytes) -> None:
        self._read_result = data

    def will_remove(self, path: str, path_type: str) -> None:
        self._remove_result = {"target_path": path, "path_type": path_type}

    def will_rmtree(self, path: str) -> None:
        self._rmtree_result = path

    def will_list_dir(self, dir_path: str, recursive: bool, files: list[dict]) -> None:
        self._list_dir_result = {
            "dir_path": dir_path,
            "recursive": recursive,
            "files": files,
        }

    def will_raise(self, exc: Exception) -> None:
        self._raise = exc

    # ── port protocol ──

    async def upload(self, target_path: str, content: bytes) -> dict:
        if self._raise:
            raise self._raise
        return self._upload_result  # type: ignore[return-value]

    async def read(self, file_path: str) -> bytes:
        if self._raise:
            raise self._raise
        return self._read_result

    async def remove(self, target_path: str) -> dict:
        if self._raise:
            raise self._raise
        return self._remove_result  # type: ignore[return-value]

    async def rmtree(self, target_path: str) -> str:
        if self._raise:
            raise self._raise
        return self._rmtree_result

    async def list_dir(self, dir_path: str, recursive: bool = False) -> dict:
        if self._raise:
            raise self._raise
        return self._list_dir_result  # type: ignore[return-value]


# ── upload ──


@pytest.mark.asyncio
async def test_upload_builds_upload_result():
    port = _FakeFilePort()
    port.will_upload("/home/admin/.openclaw/foo.txt", size=11, overwritten=False)
    adapter = OpenClawFileAdapter(port)

    result = await adapter.upload("/aidesktop/aidesktop_pre/bolt_data/u/p/openclaw/foo.txt", b"hello world")

    assert isinstance(result, UploadResult)
    assert result.target_path == "/home/admin/.openclaw/foo.txt"
    assert result.size == 11
    assert result.overwritten is False


@pytest.mark.asyncio
async def test_upload_overwrite_flag_preserved():
    port = _FakeFilePort()
    port.will_upload("/home/admin/.openclaw/bar.txt", size=3, overwritten=True)
    adapter = OpenClawFileAdapter(port)

    result = await adapter.upload("/home/admin/.openclaw/bar.txt", b"abc")
    assert result.overwritten is True


@pytest.mark.asyncio
async def test_upload_empty_path_propagates_value_error():
    port = _FakeFilePort()
    port.will_raise(ValueError("目标路径不能为空"))
    adapter = OpenClawFileAdapter(port)

    with pytest.raises(ValueError):
        await adapter.upload("", b"data")


@pytest.mark.asyncio
async def test_upload_is_directory_propagates():
    port = _FakeFilePort()
    port.will_raise(IsADirectoryError("目标路径已被目录占用"))
    adapter = OpenClawFileAdapter(port)

    with pytest.raises(IsADirectoryError):
        await adapter.upload("/home/admin/.openclaw/adir", b"data")


# ── read ──


@pytest.mark.asyncio
async def test_read_returns_bytes_passthrough():
    port = _FakeFilePort()
    port.will_read(b"\x89PNG")
    adapter = OpenClawFileAdapter(port)

    data = await adapter.read("/home/admin/.openclaw/img.png")
    assert data == b"\x89PNG"


@pytest.mark.asyncio
async def test_read_empty_path_returns_empty_bytes():
    port = _FakeFilePort()
    port.will_read(b"")
    adapter = OpenClawFileAdapter(port)

    data = await adapter.read("")
    assert data == b""


@pytest.mark.asyncio
async def test_read_file_not_found_propagates():
    port = _FakeFilePort()
    port.will_raise(FileNotFoundError("文件不存在"))
    adapter = OpenClawFileAdapter(port)

    with pytest.raises(FileNotFoundError):
        await adapter.read("/home/admin/.openclaw/missing.txt")


# ── remove ──


@pytest.mark.asyncio
async def test_remove_file_builds_remove_result():
    port = _FakeFilePort()
    port.will_remove("/home/admin/.openclaw/foo.txt", "file")
    adapter = OpenClawFileAdapter(port)

    result = await adapter.remove("/home/admin/.openclaw/foo.txt")
    assert isinstance(result, RemoveResult)
    assert result.target_path == "/home/admin/.openclaw/foo.txt"
    assert result.path_type == "file"


@pytest.mark.asyncio
async def test_remove_directory_builds_remove_result():
    port = _FakeFilePort()
    port.will_remove("/home/admin/.openclaw/subdir", "directory")
    adapter = OpenClawFileAdapter(port)

    result = await adapter.remove("/home/admin/.openclaw/subdir")
    assert result.path_type == "directory"


@pytest.mark.asyncio
async def test_remove_not_found_propagates():
    port = _FakeFilePort()
    port.will_raise(FileNotFoundError("路径不存在"))
    adapter = OpenClawFileAdapter(port)

    with pytest.raises(FileNotFoundError):
        await adapter.remove("/home/admin/.openclaw/ghost.txt")


# ── rmtree ──


@pytest.mark.asyncio
async def test_rmtree_returns_resolved_path():
    port = _FakeFilePort()
    port.will_rmtree("/home/admin/.openclaw/workspace")
    adapter = OpenClawFileAdapter(port)

    path = await adapter.rmtree("/home/admin/.openclaw/workspace")
    assert path == "/home/admin/.openclaw/workspace"


@pytest.mark.asyncio
async def test_rmtree_not_a_directory_propagates():
    port = _FakeFilePort()
    port.will_raise(NotADirectoryError("路径不是目录"))
    adapter = OpenClawFileAdapter(port)

    with pytest.raises(NotADirectoryError):
        await adapter.rmtree("/home/admin/.openclaw/file.txt")


# ── list_dir ──


def _file_entry_dict(name: str, is_dir: bool = False, size: int = 0) -> dict:
    return {
        "name": name,
        "path": f"/home/admin/.openclaw/{name}",
        "relative_path": name,
        "is_dir": is_dir,
        "size": size,
    }


@pytest.mark.asyncio
async def test_list_dir_builds_list_dir_result_with_file_entries():
    port = _FakeFilePort()
    port.will_list_dir(
        "/home/admin/.openclaw",
        recursive=False,
        files=[
            _file_entry_dict("a.txt", size=5),
            _file_entry_dict("subdir", is_dir=True),
        ],
    )
    adapter = OpenClawFileAdapter(port)

    result = await adapter.list_dir("/home/admin/.openclaw")
    assert isinstance(result, ListDirResult)
    assert result.dir_path == "/home/admin/.openclaw"
    assert result.recursive is False
    assert len(result.files) == 2
    assert isinstance(result.files[0], FileEntry)
    assert result.files[0].name == "a.txt"
    assert result.files[0].size == 5
    assert result.files[1].is_dir is True


@pytest.mark.asyncio
async def test_list_dir_recursive_flag_preserved():
    port = _FakeFilePort()
    port.will_list_dir("/home/admin/.openclaw", recursive=True, files=[])
    adapter = OpenClawFileAdapter(port)

    result = await adapter.list_dir("/home/admin/.openclaw", recursive=True)
    assert result.recursive is True
    assert result.files == []


@pytest.mark.asyncio
async def test_list_dir_not_found_propagates():
    port = _FakeFilePort()
    port.will_raise(FileNotFoundError("目录不存在"))
    adapter = OpenClawFileAdapter(port)

    with pytest.raises(FileNotFoundError):
        await adapter.list_dir("/home/admin/.openclaw/missing")


@pytest.mark.asyncio
async def test_list_dir_not_a_directory_propagates():
    port = _FakeFilePort()
    port.will_raise(NotADirectoryError("路径不是目录"))
    adapter = OpenClawFileAdapter(port)

    with pytest.raises(NotADirectoryError):
        await adapter.list_dir("/home/admin/.openclaw/file.txt")
