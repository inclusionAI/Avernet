"""``LocalCliToolsService`` — CLI tools as files in a directory.

Engine-agnostic on purpose. Placing a command is local filesystem work — write
the file, set the executable bit, list, delete — with no gateway call and no
per-engine protocol anywhere in it. The one thing that differs between engines
is *which* directory, so that is the one thing injected. Compare ``skills``,
which needs a per-engine adapter because it relays through each engine's
gateway; this needs none, and four near-identical adapters differing by a
constant would be the cost of pretending otherwise.

**No ``sha256`` verification lives here, deliberately.** The platform already
enforced the user's pin before any byte reached delivery (§4 A2: *「不要重复校验
sha256」*). Re-checking would give a pinned hash two enforcement points, which
is how the two drift and how a tool starts failing on one side only. The
``md5`` this module computes is a *change* test for drift observation, never an
integrity gate.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from engine.community.core.cli_tools.models import (
    CliToolBytes,
    CliToolInfo,
    CliToolPayload,
    CliToolResult,
)
from engine.community.core.cli_tools.protocol import CliToolsService

log = logging.getLogger("engine.cli_tools")

#: Owner rwx, group/other rx — runnable by the agent, writable only by the
#: account the engine runs as.
TOOL_MODE = 0o755


class InvalidCliToolNameError(ValueError):
    """A name that cannot address a file in the tool directory."""


def validate_tool_name(name: str) -> str:
    """Return ``name`` if it can only ever name a file *inside* the directory.

    The platform validates names too (uniqueness per bot, no separators), but
    this service builds a filesystem path out of the value, so it re-checks
    rather than trusting a caller. That is not duplicated policy: the
    platform's rule is about a bot's command namespace, this one is a
    path-traversal guard on a write.
    """
    if not name or name != name.strip():
        raise InvalidCliToolNameError(f"cli tool name is empty or padded: {name!r}")
    if name in (".", ".."):
        raise InvalidCliToolNameError(f"cli tool name is a directory link: {name!r}")
    if "/" in name or "\\" in name or "\0" in name:
        raise InvalidCliToolNameError(
            f"cli tool name contains a path separator: {name!r}"
        )
    if Path(name).name != name:
        raise InvalidCliToolNameError(f"cli tool name is not a bare filename: {name!r}")
    return name


def _md5(data: bytes) -> str:
    # Not a security boundary — a change test for drift observation. The
    # platform owns integrity via the user's pinned sha256.
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


class LocalCliToolsService(CliToolsService):
    """CLI tools kept as executable files under one directory.

    ``directory`` is a callable, not a ``Path``: OpenClaw's resolves an
    environment variable BaaS injects at spawn time, so binding a value at
    construction would capture whatever was set when the engine object was
    built rather than what is true at the call.
    """

    def __init__(self, directory: Callable[[], Path]) -> None:
        self._directory = directory

    # ── the directory ────────────────────────────────────────────────────

    def _dir(self) -> Path:
        return self._directory()

    def _path_of(self, name: str) -> Path:
        return self._dir() / validate_tool_name(name)

    # ── writes ───────────────────────────────────────────────────────────

    async def install(self, name: str, data: bytes) -> None:
        await asyncio.to_thread(self._install_sync, name, data)

    def _install_sync(self, name: str, data: bytes) -> None:
        """Write, chmod, then rename — in that order, and atomically.

        The rename is what makes this safe to interrupt: until it runs the
        target either does not exist or still holds the previous tool, so a
        half-written file is never reachable under the command's name. The
        temp file is created in the destination directory so the rename stays
        within one filesystem, where ``os.replace`` is atomic.
        """
        target = self._path_of(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{name}.", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, TOOL_MODE)
            os.replace(tmp, target)
        except BaseException:
            # Leave nothing runnable behind, then let the caller see the
            # failure — an install that did not land must never be reported
            # as installed.
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
        log.info(
            "[cli_tools] installed name=%s size=%d dir=%s",
            name, len(data), target.parent,
        )

    async def delete(self, name: str) -> None:
        await asyncio.to_thread(self._delete_sync, name)

    def _delete_sync(self, name: str) -> None:
        """Remove the command. Absent is success, not an error (§4 A2)."""
        try:
            self._path_of(name).unlink()
        except FileNotFoundError:
            log.info("[cli_tools] delete name=%s: already absent", name)
            return
        log.info("[cli_tools] deleted name=%s", name)

    async def replace_all(
        self, tools: Sequence[CliToolPayload]
    ) -> list[CliToolResult]:
        return await asyncio.to_thread(self._replace_all_sync, tools)

    def _replace_all_sync(
        self, tools: Sequence[CliToolPayload]
    ) -> list[CliToolResult]:
        """Install everything named, *then* prune everything not named.

        The ordering is the point of this endpoint existing. Pruning first
        would publish an intermediate state in which a tool the request keeps
        is briefly gone — precisely the window the platform avoids by not
        looping ``delete``/``install`` itself.

        A name that failed to install is **not** pruned either: its old binary
        is left in place, so a failed replacement degrades to "unchanged"
        rather than "removed".
        """
        results: list[CliToolResult] = []
        for tool in tools:
            try:
                self._install_sync(tool.name, tool.data)
            except Exception as error:  # noqa: BLE001 — reported per name
                log.warning(
                    "[cli_tools] replace: install failed name=%s: %s",
                    tool.name, error,
                )
                results.append(
                    CliToolResult(name=tool.name, success=False, message=str(error))
                )
                continue
            results.append(CliToolResult(name=tool.name, success=True))

        keep = {result.name for result in results}
        for present in self._names_on_disk():
            if present not in keep:
                self._delete_sync(present)
        return results

    # ── reads ────────────────────────────────────────────────────────────

    async def list_tools(self) -> list[CliToolInfo]:
        return await asyncio.to_thread(self._list_sync)

    def _list_sync(self) -> list[CliToolInfo]:
        """Read the directory now. No cache, no replay of the last write."""
        infos: list[CliToolInfo] = []
        for name in sorted(self._names_on_disk()):
            path = self._dir() / name
            try:
                data = path.read_bytes()
            except (FileNotFoundError, IsADirectoryError, PermissionError):
                # Raced with a delete, or something unreadable landed in the
                # directory. Either way it is not a command the bot can run.
                continue
            infos.append(
                CliToolInfo(name=name, md5=_md5(data), size_bytes=len(data))
            )
        return infos

    async def read_tool(self, name: str) -> CliToolBytes | None:
        return await asyncio.to_thread(self._read_sync, name)

    def _read_sync(self, name: str) -> CliToolBytes | None:
        try:
            data = self._path_of(name).read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            return None
        return CliToolBytes(name=name, data=data, md5=_md5(data))

    # ── shared ───────────────────────────────────────────────────────────

    def _names_on_disk(self) -> list[str]:
        """Command names present in the directory, ignoring in-flight writes.

        A missing directory reads as "no commands" rather than an error: a bot
        that never had a tool installed has no directory yet, and that is an
        ordinary state, not a failure.
        """
        directory = self._dir()
        try:
            entries = list(os.scandir(directory))
        except FileNotFoundError:
            return []
        return [
            entry.name
            for entry in entries
            if entry.is_file() and not entry.name.endswith(".part")
        ]


__all__ = [
    "TOOL_MODE",
    "InvalidCliToolNameError",
    "LocalCliToolsService",
    "validate_tool_name",
]
