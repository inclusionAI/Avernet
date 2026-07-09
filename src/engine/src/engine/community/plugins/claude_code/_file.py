"""_FilePortMixin — file operations via relay ``file.*`` RPCs.

The corp ``engines/claude_code/file.py`` performs local filesystem I/O with
legacy OSS-view path-prefix rewriting. In the ACL split the path-prefix
translation is an adapter concern; the community port forwards ``file.*`` RPCs
to the relay so file ops land on the relay's own filesystem (the same place
the Claude subprocess ``cwd`` lives). Each method returns the raw relay
payload / bool the adapter wraps into DTOs.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("claude-code-community-port")


class _FilePortMixin:
    """Domain mixin: file.{upload,read,remove,rmtree,list}."""

    async def file_upload(self, path: str,
                          content_bytes: bytes | None = None,
                          token: str | None = None) -> dict:
        params: dict[str, Any] = {"path": path}
        if content_bytes is not None:
            params["content"] = content_bytes
        resp = await (await self._relay()).send_request("file.upload", params)
        if resp.ok:
            return resp.payload if isinstance(resp.payload, dict) else {"path": path}
        raise RuntimeError(
            f"file.upload failed: "
            f"{resp.error.message if resp.error else 'unknown'}")

    async def file_read(self, path: str, token: str | None = None) -> dict:
        resp = await (await self._relay()).send_request("file.read", {"path": path})
        if not resp.ok:
            raise FileNotFoundError(
                f"file.read failed: "
                f"{resp.error.message if resp.error else 'unknown'}")
        return resp.payload if isinstance(resp.payload, dict) else {"path": path}

    async def file_remove(self, path: str, token: str | None = None) -> bool:
        resp = await (await self._relay()).send_request("file.remove", {"path": path})
        return bool(resp.ok)

    async def file_rmtree(self, path: str, token: str | None = None) -> bool:
        resp = await (await self._relay()).send_request("file.rmtree", {"path": path})
        return bool(resp.ok)

    async def file_list_dir(self, path: str, token: str | None = None) -> list[dict]:
        resp = await (await self._relay()).send_request("file.list", {"path": path})
        if not resp.ok:
            return []
        payload = resp.payload or {}
        entries = payload.get("entries", []) if isinstance(payload, dict) else payload
        return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []
