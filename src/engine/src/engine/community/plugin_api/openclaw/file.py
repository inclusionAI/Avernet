"""OpenClawFilePort — native port for workspace filesystem operations.

File operations are local-infra: they work directly on the pod filesystem
(``/home/admin/.openclaw/``) — no gateway, no pool, no token.  The
``_convert_path`` path-rewrite logic lives in the port impl (it is
a transport concern); the adapter builds core DTOs from the primitive
dicts/bytes returned here.
"""
from __future__ import annotations

from typing import Any, Protocol


class OpenClawFilePort(Protocol):
    """Native filesystem operations over the OpenClaw workspace."""

    async def upload(self, target_path: str, content: bytes) -> dict[str, Any]:
        """Write ``content`` to ``target_path`` (after path rewrite).

        Returns a primitive dict with keys:
          ``target_path`` (str), ``size`` (int), ``overwritten`` (bool).
        Raises ``ValueError`` for an empty path, ``IsADirectoryError``
        when the target is an existing directory.
        """
        ...

    async def read(self, file_path: str) -> bytes:
        """Read the file at ``file_path`` (after path rewrite) as bytes.

        Returns ``b""`` for an empty path.  Raises ``FileNotFoundError``
        when the resolved path does not exist or is not a regular file.
        """
        ...

    async def remove(self, target_path: str) -> dict[str, Any]:
        """Delete a single file or directory at ``target_path``.

        Returns a primitive dict with keys:
          ``target_path`` (str), ``path_type`` (``"file"`` | ``"directory"``).
        Raises ``ValueError`` for an empty path, ``FileNotFoundError``
        when the path does not exist, or ``ValueError`` for an unsupported
        path type.
        """
        ...

    async def rmtree(self, target_path: str) -> str:
        """Recursively remove the directory at ``target_path``.

        Returns the resolved (post-rewrite) path string.
        Raises ``ValueError`` for an empty path, ``FileNotFoundError``
        when the path does not exist, ``NotADirectoryError`` when the
        path is not a directory.
        """
        ...

    async def list_dir(
        self, dir_path: str, recursive: bool = False, exclude_dirs: set[str] | None = None
    ) -> dict[str, Any]:
        """List the contents of ``dir_path``.

        Returns a primitive dict with keys:
          ``dir_path`` (str), ``recursive`` (bool),
          ``files`` (list[dict]) — each entry has
          ``name``, ``path``, ``relative_path``, ``is_dir``, ``size``.
        Raises ``ValueError`` for an empty path, ``FileNotFoundError``
        when the path does not exist, ``NotADirectoryError`` when the
        path is not a directory.
        """
        ...


__all__ = ["OpenClawFilePort"]
