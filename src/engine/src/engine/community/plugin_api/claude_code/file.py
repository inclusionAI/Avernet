"""ClaudeCodeFilePort — native port for file operations.

File ops are pooled (client + relay), so port methods take
``token: str | None = None`` for per-token routing. Returns raw
dicts / list[dict] / bool — the adapter builds the core DTOs.

Relay RPC mapping (teamclaw-aicoding-relay v3 protocol):

==========================  ================================================
Port method                 Relay RPC (method name on the wire)
==========================  ================================================
``file_upload``             ``file.upload``
``file_read``               ``file.read``
``file_remove``             ``file.remove``
``file_rmtree``             ``file.rmtree``
``file_list_dir``           ``file.list``
==========================  ================================================
"""
from __future__ import annotations

from typing import Protocol


class ClaudeCodeFilePort(Protocol):
    """Native file operations over the claude_code gateway (vendored Node relay)."""

    async def file_upload(
        self,
        path: str,
        content_bytes: bytes | None = None,
        token: str | None = None,
    ) -> dict:
        """Call ``file.upload`` to upload file content.

        Args:
            path: Target file path on the relay filesystem.
            content_bytes: Optional raw bytes to write; None reads from source.
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw upload result dict.
        """
        ...

    async def file_read(
        self,
        path: str,
        token: str | None = None,
    ) -> dict:
        """Call ``file.read``; return dict with file content.

        Args:
            path: Source file path to read.
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw dict containing file content and metadata.
        """
        ...

    async def file_remove(
        self,
        path: str,
        token: str | None = None,
    ) -> bool:
        """Call ``file.remove``; return True on success, False on error.

        Args:
            path: File path to remove.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def file_rmtree(
        self,
        path: str,
        token: str | None = None,
    ) -> bool:
        """Call ``file.rmtree`` to remove a directory tree; return bool.

        Args:
            path: Directory path to remove recursively.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def file_list_dir(
        self,
        path: str,
        token: str | None = None,
    ) -> list[dict]:
        """Call ``file.list``; return raw entry dicts for directory contents.

        Args:
            path: Directory path to list.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...


__all__ = ["ClaudeCodeFilePort"]
