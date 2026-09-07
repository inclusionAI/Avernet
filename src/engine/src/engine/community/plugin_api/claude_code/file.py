"""Claude Code native file port (v2): Engine-local filesystem operations.

The community implementation shares a filesystem with the Claude runtime.
Paths are engine-view absolute addresses. Transport/NAS addressing belongs to
provider boundaries, not to callers or the core adapter. ``token`` is retained
for caller compatibility, not interpreted as file authorization.

All failures raise standard filesystem exceptions; False/empty payloads must
not disguise missing files, permission failures or invalid responses. No relay
connection is required. Other Claude Code capabilities may still use a relay.
"""

from __future__ import annotations
from typing import Protocol


class ClaudeCodeFilePort(Protocol):
    async def file_upload(
        self, path: str, content_bytes: bytes, token: str | None = None
    ) -> dict:
        """Write raw bytes; return path, size and overwritten."""
        ...

    async def file_read(self, path: str, token: str | None = None) -> dict:
        """Return path and raw bytes in content; absence raises FileNotFoundError."""
        ...

    async def file_remove(self, path: str, token: str | None = None) -> dict:
        """Remove a file/tree; return target_path and path_type. Unlink symlinks."""
        ...

    async def file_rmtree(self, path: str, token: str | None = None) -> bool:
        """Remove a tree, file or link; return True, or raise on failure."""
        ...

    async def file_list_dir(
        self,
        path: str,
        token: str | None = None,
        *,
        recursive: bool = False,
        exclude_dirs: set[str] | None = None,
    ) -> list[dict]:
        """List entries with name/path/relative_path/is_dir/size.

        Missing directories raise; empty directories return []. Recursive
        listings never follow directory symlinks. exclude_dirs contains names.
        """
        ...


__all__ = ["ClaudeCodeFilePort"]
