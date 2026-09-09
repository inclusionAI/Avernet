"""Claude Code FileService adapter over the native file port.

Only parameter/result conversion lives here. The community concrete port does
local Engine-filesystem I/O; it raises standard filesystem exceptions for the
HTTP router to map. Recursion and exclusions must reach the port intact.
"""

from __future__ import annotations

import logging
from typing import Any

from engine.community.core.engine.context import AuthContext
from engine.community.core.file.models import (
    FileEntry,
    ListDirResult,
    RemoveResult,
    UploadResult,
)
from engine.community.core.file.protocol import FileService
from engine.community.plugin_api.claude_code.file import ClaudeCodeFilePort

log = logging.getLogger("claude-code-file-adapter")


def _to_file_entry(data: dict[str, Any]) -> FileEntry:
    return FileEntry(
        name=data.get("name", ""),
        path=data.get("path", ""),
        relative_path=data.get("relative_path", data.get("relativePath", "")),
        is_dir=bool(data.get("is_dir", data.get("isDir", False))),
        size=int(data.get("size", 0) or 0),
    )


def _extract_bytes(raw: Any) -> bytes:
    """Coerce a port read-result (dict / str / bytes) into bytes."""
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        return raw.encode("utf-8")
    if isinstance(raw, dict):
        content = raw.get("content")
        if isinstance(content, bytes):
            return content
        if isinstance(content, str):
            return content.encode("utf-8")
    raise ValueError("File port returned invalid content")


class ClaudeCodeFileAdapter(FileService):
    """`FileService` over the claude_code native file port."""

    def __init__(self, port: ClaudeCodeFilePort) -> None:
        self._port = port

    async def upload(
        self,
        target_path: str,
        content: bytes,
        auth: AuthContext | None = None,
    ) -> UploadResult:
        token = auth.token if auth is not None else None
        raw = await self._port.file_upload(
            path=target_path, content_bytes=content, token=token
        )
        return UploadResult(
            target_path=raw.get("target_path", raw.get("path", target_path)),
            size=int(raw.get("size", len(content)) or 0),
            overwritten=bool(raw.get("overwritten", False)),
        )

    async def read(
        self,
        file_path: str,
        auth: AuthContext | None = None,
    ) -> bytes:
        token = auth.token if auth is not None else None
        raw = await self._port.file_read(path=file_path, token=token)
        return _extract_bytes(raw)

    async def remove(
        self,
        target_path: str,
        auth: AuthContext | None = None,
    ) -> RemoveResult:
        token = auth.token if auth is not None else None
        raw = await self._port.file_remove(path=target_path, token=token)
        return RemoveResult(target_path=raw["target_path"], path_type=raw["path_type"])

    async def rmtree(
        self,
        target_path: str,
        auth: AuthContext | None = None,
    ) -> str:
        """Recursively remove a directory. Returns the resolved path.

        The port returns ``bool``; on success we return the target path
        (the core protocol expects ``str``). On failure raise
        FileNotFoundError so the router maps to 404.
        """
        token = auth.token if auth is not None else None
        ok = await self._port.file_rmtree(path=target_path, token=token)
        if not ok:
            raise FileNotFoundError(f"rmtree failed: {target_path}")
        return target_path

    async def list_dir(
        self,
        dir_path: str,
        recursive: bool = False,
        exclude_dirs: set[str] | None = None,
        auth: AuthContext | None = None,
    ) -> ListDirResult:
        token = auth.token if auth is not None else None
        raw_entries = await self._port.file_list_dir(
            path=dir_path, token=token, recursive=recursive, exclude_dirs=exclude_dirs
        )
        files = [_to_file_entry(e) for e in raw_entries if isinstance(e, dict)]
        return ListDirResult(
            dir_path=dir_path,
            recursive=recursive,
            files=files,
        )


__all__ = ["ClaudeCodeFileAdapter"]
