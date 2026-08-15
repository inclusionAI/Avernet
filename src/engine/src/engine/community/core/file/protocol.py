"""
FileService Protocol — engine-managed filesystem operations.

Engines that own a workspace (OpenClaw under
``/home/admin/.openclaw/...``) implement this Protocol. The router
collects HTTP form data / multipart uploads and hands paths +
``bytes`` to the plugin; the plugin owns path translation and the
actual FS calls.

Two address formats are accepted on every path argument (#1000):

* **namespace-relative** — ``workspace/<rel>`` · ``identity/<rel>`` ·
  ``config/<rel>``, a leading slash optional. The caller names a logical
  location; the engine resolves it against its own layout and refuses
  anything that escapes the namespace. This is the format new callers
  should use, and the only one that does not require the caller to know
  the container layout.
* **absolute** — an OSS-view ``/aidesktop/...`` path the engine remaps, or
  an already-engine-view path it uses as-is. Fully supported; this is what
  today's callers send.

A relative path with no namespace prefix is a :class:`ValueError`. It used
to resolve against the engine process's working directory, which silently
put the bytes outside the workspace (#1000).

Errors are raised as standard exceptions; the router maps them to
HTTP codes:

* :class:`FileNotFoundError` → 404
* :class:`FileExistsError` / :class:`IsADirectoryError` → 409
* :class:`NotADirectoryError` → 400
* :class:`ValueError` → 400
* :class:`PermissionError` → 403
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.core.engine.context import AuthContext
from engine.community.core.file.models import ListDirResult, RemoveResult, UploadResult


@runtime_checkable
class FileService(Protocol):
    """Backend talks to filesystem-aware engines through this Protocol."""

    async def upload(
        self,
        target_path: str,
        content: bytes,
        auth: AuthContext | None = None,
    ) -> UploadResult:
        """Persist `content` at `target_path`. Returns the final on-disk
        path (may differ when the engine remaps prefixes)."""
        ...

    async def read(
        self, file_path: str, auth: AuthContext | None = None,
    ) -> bytes:
        """Read the entire file content as bytes.

        Today this loads the file into memory — fine for the workspace
        files the router currently serves (configs, manifests, small
        artifacts). Switch to an async iterator if/when the use cases
        grow.
        """
        ...

    async def remove(
        self,
        target_path: str,
        auth: AuthContext | None = None,
    ) -> RemoveResult:
        """Delete a single file or recursively remove a directory."""
        ...

    async def rmtree(
        self,
        target_path: str,
        auth: AuthContext | None = None,
    ) -> str:
        """Recursively remove a directory. Returns the resolved path."""
        ...

    async def list_dir(
        self,
        dir_path: str,
        recursive: bool = False,
        exclude_dirs: set[str] | None = None,
        auth: AuthContext | None = None,
    ) -> ListDirResult:
        """List the contents of a directory."""
        ...


__all__ = ["FileService"]
