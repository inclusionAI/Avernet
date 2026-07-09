"""OpenClaw file ACL adapter.

Implements the core ``FileService`` by delegating to an injected
``OpenClawFilePort`` and translating the port's primitive dicts/bytes into
core DTOs.  The path-rewrite logic and all FS operations live in the port
impl (leaf side); this adapter only constructs UploadResult / RemoveResult /
ListDirResult / FileEntry from the returned primitives.

Exceptions raised by the port (ValueError, FileNotFoundError,
IsADirectoryError, NotADirectoryError) propagate unchanged — the router
maps them to HTTP status codes as documented in core/file/protocol.py.
"""
from __future__ import annotations

from typing import Any

from engine.community.core.engine.context import AuthContext
from engine.community.core.file.models import (
    FileEntry,
    ListDirResult,
    RemoveResult,
    UploadResult,
)
from engine.community.core.file.protocol import FileService
from engine.community.plugin_api.openclaw.file import OpenClawFilePort


class OpenClawFileAdapter(FileService):
    """`FileService` over the OpenClaw native port."""

    def __init__(self, port: OpenClawFilePort) -> None:
        self._port = port

    async def upload(
        self,
        target_path: str,
        content: bytes,
        auth: AuthContext | None = None,
    ) -> UploadResult:
        raw = await self._port.upload(target_path, content)
        return UploadResult(
            target_path=raw["target_path"],
            size=raw["size"],
            overwritten=raw["overwritten"],
        )

    async def read(
        self,
        file_path: str,
        auth: AuthContext | None = None,
    ) -> bytes:
        # bytes pass through — no DTO wrapping needed.
        return await self._port.read(file_path)

    async def remove(
        self,
        target_path: str,
        auth: AuthContext | None = None,
    ) -> RemoveResult:
        raw = await self._port.remove(target_path)
        return RemoveResult(
            target_path=raw["target_path"],
            path_type=raw["path_type"],
        )

    async def rmtree(
        self,
        target_path: str,
        auth: AuthContext | None = None,
    ) -> str:
        # str passes through — no DTO wrapping needed.
        return await self._port.rmtree(target_path)

    async def list_dir(
        self,
        dir_path: str,
        recursive: bool = False,
        exclude_dirs: set[str] | None = None,
        auth: AuthContext | None = None,
    ) -> ListDirResult:
        raw = await self._port.list_dir(dir_path, recursive)
        files = [_to_file_entry(e) for e in raw["files"]]
        return ListDirResult(
            dir_path=raw["dir_path"],
            recursive=raw["recursive"],
            files=files,
        )


def _to_file_entry(data: dict[str, Any]) -> FileEntry:
    """Build a ``FileEntry`` from a primitive file-info dict."""
    return FileEntry(
        name=data["name"],
        path=data["path"],
        relative_path=data["relative_path"],
        is_dir=data["is_dir"],
        size=data.get("size", 0),
    )


__all__ = ["OpenClawFileAdapter"]
