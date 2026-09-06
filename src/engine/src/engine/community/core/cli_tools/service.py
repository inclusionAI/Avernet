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
import stat
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from engine.community.core.cli_tools.models import (
    CliToolBytes,
    CliToolInfo,
    CliToolPayload,
    CliToolResult,
    ReplaceOutcome,
)
from engine.community.core.cli_tools.protocol import CliToolsService

log = logging.getLogger("engine.cli_tools")

#: Owner rwx, group/other rx — runnable by the agent, writable only by the
#: account the engine runs as.
TOOL_MODE = 0o755


#: Longest name ``install`` can actually place. ``mkstemp`` builds a scratch
#: name of ``.{name}.XXXXXXXX.part`` — 15 characters more than the tool's own —
#: and most filesystems cap a component at 255 bytes. Bounding the *name* here
#: keeps every operation consistent: without it a 250-character name would pass
#: validation, delete and list fine, and fail only on install, which would show
#: up as a permanently failing entry in an apply report with no obvious cause.
MAX_NAME_LENGTH = 240


#: Scratch files are named ``.{tool}.{random}.part``. **Both** halves identify
#: one, and the leading dot is the half that matters: the platform's own name
#: rule requires a tool name to start with an alphanumeric
#: (``bot_config_manifest/schema/_support.py``), so no real tool can begin with
#: a dot — while ``.part`` on its own is a legal ending for one. Matching the
#: suffix alone would make a tool called ``unpack.part`` invisible to listing
#: and immune to pruning, so it would sit on the bot forever after the user
#: removed it from the manifest.
SCRATCH_PREFIX = "."
SCRATCH_SUFFIX = ".part"


def _is_scratch(name: str) -> bool:
    return name.startswith(SCRATCH_PREFIX) and name.endswith(SCRATCH_SUFFIX)


class InvalidCliToolNameError(ValueError):
    """A name that cannot address a file in the tool directory."""


def validate_tool_name(name: str) -> str:
    """Return ``name`` if it can only ever name a file *inside* the directory.

    The platform validates names too (uniqueness per bot, no separators), but
    this service builds a filesystem path out of the value, so it re-checks
    rather than trusting a caller. That is not duplicated policy: the
    platform's rule is about a bot's command namespace, this one is a
    path-traversal guard on a write.

    **Applies to caller input only.** Names read back off the disk are not
    routed through here — see :meth:`LocalCliToolsService._prune_path`.
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
    if len(name.encode("utf-8")) > MAX_NAME_LENGTH:
        raise InvalidCliToolNameError(
            f"cli tool name is longer than {MAX_NAME_LENGTH} bytes: {name!r}"
        )
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
        # Mutating operations are serialised. Without this, a `replace_all`
        # that has already installed its set can prune a tool a concurrent
        # `install` just reported as placed: the platform records that tool as
        # installed and the bot does not have it — the "silence read as
        # success" failure the contract exists to prevent. Overlapping calls
        # are reachable in practice, because a client-side timeout does not
        # stop the request already running here.
        self._write_lock = asyncio.Lock()

    # ── the directory ────────────────────────────────────────────────────

    def _dir(self) -> Path:
        return self._directory()

    def _path_of(self, name: str) -> Path:
        return self._dir() / validate_tool_name(name)

    # ── writes ───────────────────────────────────────────────────────────

    async def install(self, name: str, data: bytes) -> None:
        validate_tool_name(name)
        async with self._write_lock:
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
        fd, tmp = tempfile.mkstemp(
            dir=str(target.parent), prefix=f"{SCRATCH_PREFIX}{name}.", suffix=SCRATCH_SUFFIX
        )
        try:
            # ``fdopen`` takes ownership of the descriptor, but only once it
            # succeeds — if it raises, nothing else would ever close ``fd``.
            handle = os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)
            self._unlink_quietly(Path(tmp))
            raise
        try:
            with handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, TOOL_MODE)
            os.replace(tmp, target)
        except BaseException:
            # Leave nothing runnable behind, then let the caller see the
            # failure — an install that did not land must never be reported
            # as installed.
            self._unlink_quietly(Path(tmp))
            raise
        self._fsync_dir(target.parent)
        log.info(
            "[cli_tools] installed name=%s size=%d dir=%s",
            name, len(data), target.parent,
        )

    async def delete(self, name: str) -> None:
        validate_tool_name(name)
        async with self._write_lock:
            await asyncio.to_thread(self._delete_sync, name)

    def _delete_sync(self, name: str) -> None:
        """Remove a **caller-named** command. Absent is success (§4 A2).

        Anything *else* that stops the removal — a read-only filesystem, lost
        permissions, an immutable file — is raised, never swallowed. Reporting
        success while the executable is still callable would let the apply
        report claim a tool was removed when the bot can still run it.
        """
        path = self._path_of(name)
        try:
            path.unlink()
        except FileNotFoundError:
            log.info("[cli_tools] delete name=%s: already absent", name)
            return
        log.info("[cli_tools] deleted name=%s", name)

    def _prune_path(self, path: Path) -> str | None:
        """Remove one *disk-sourced* file. Returns a reason on failure.

        Names here came from ``scandir``, not from a caller, so they are not
        put through :func:`validate_tool_name`: a file the agent created inside
        the container may carry a name the validator refuses (trailing space, a
        backslash). Re-validating would raise out of the prune loop, abandoning
        the rest of it — and the offending file, undeletable by that path,
        would make every future replacement for that bot fail the same way.

        A failure is returned rather than raised so one stubborn entry cannot
        abort the loop, and rather than logged-and-forgotten so the caller can
        report that the tool is still there.
        """
        try:
            path.unlink()
        except FileNotFoundError:
            return None
        except OSError as error:
            log.warning("[cli_tools] could not remove %s: %s", path, error)
            return f"{path.name}: {error}"
        log.info("[cli_tools] deleted %s", path.name)
        return None

    @staticmethod
    def _unlink_quietly(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        """Persist the rename itself, not just the bytes it points at.

        Without this a machine-level crash right after a successful install
        can lose the directory entry and leave the previous binary in place.
        Best-effort: some filesystems refuse to open a directory for sync.
        """
        try:
            fd = os.open(str(directory), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    async def replace_all(
        self,
        tools: Sequence[CliToolPayload],
        *,
        also_keep: Sequence[str] = (),
    ) -> ReplaceOutcome:
        async with self._write_lock:
            return await asyncio.to_thread(
                self._replace_all_sync, tools, tuple(also_keep)
            )

    def _replace_all_sync(
        self, tools: Sequence[CliToolPayload], also_keep: Sequence[str] = ()
    ) -> ReplaceOutcome:
        """Install everything named, *then* prune everything not named.

        The ordering is the point of this endpoint existing. Pruning first
        would publish an intermediate state in which a tool the request keeps
        is briefly gone — precisely the window the platform avoids by not
        looping ``delete``/``install`` itself.

        A name that failed to install is **not** pruned either: its old binary
        is left in place, so a failed replacement degrades to "unchanged"
        rather than "removed". ``also_keep`` extends that to names the *caller*
        could not prepare — an entry whose payload would not decode never
        reaches this method, and without it the prune would read that name as
        dropped from the set and delete a perfectly good installed tool.
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

        keep = {result.name for result in results} | set(also_keep)
        directory = self._dir()
        prune_failures: list[str] = []
        for present in self._names_on_disk():
            if present not in keep:
                failure = self._prune_path(directory / present)
                if failure is not None:
                    prune_failures.append(failure)
        return ReplaceOutcome(results=results, prune_failures=prune_failures)

    # ── reads ────────────────────────────────────────────────────────────

    async def list_tools(self) -> list[CliToolInfo]:
        return await asyncio.to_thread(self._list_sync)

    def _list_sync(self) -> list[CliToolInfo]:
        """Read the directory now. No cache, no replay of the last write."""
        infos: list[CliToolInfo] = []
        for name in sorted(self._names_on_disk()):
            data = self._read_bytes_nofollow(self._dir() / name)
            if data is None:
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
        data = self._read_bytes_nofollow(self._path_of(name))
        if data is None:
            return None
        return CliToolBytes(name=name, data=data, md5=_md5(data))

    # ── shared ───────────────────────────────────────────────────────────

    @staticmethod
    def _read_bytes_nofollow(path: Path) -> bytes | None:
        """Read a file, refusing to follow a symlink out of the directory.

        Validating the *name* bounds the path this service builds; it says
        nothing about what that path points at. The tool directory lives
        inside the bot's container, where the agent can create a symlink, so
        following one here would turn the download endpoint into an arbitrary
        read of any file the engine account can open. ``O_NOFOLLOW`` makes the
        final component's symlink an error, and a link reads as "no such
        tool", which is exactly what it is.
        """
        try:
            # O_NONBLOCK so a FIFO cannot park this thread. Opening a named
            # pipe for reading blocks until a writer appears, and the tool
            # directory is inside the bot's container — so an agent that
            # created a FIFO there could hold a worker from the shared
            # ``to_thread`` executor on every download, and enough of them
            # would stall every CLI operation. It is a no-op for regular
            # files, which are the only thing this goes on to read.
            fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            # ELOOP (a symlink), ENOENT, EISDIR, EACCES — none of them is a
            # command the bot can run.
            return None
        try:
            with os.fdopen(fd, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    # A FIFO, socket or device. Not a command either.
                    return None
                return handle.read()
        except OSError:
            return None

    def _names_on_disk(self) -> list[str]:
        """Command names present in the directory, ignoring in-flight writes.

        A missing directory reads as "no commands" rather than an error: a bot
        that never had a tool installed has no directory yet, and that is an
        ordinary state, not a failure.

        ``follow_symlinks=False`` so a link to a regular file elsewhere is not
        counted as a tool — the read side refuses it anyway, and counting it
        would report a command that cannot be downloaded.
        """
        directory = self._dir()
        try:
            entries = list(os.scandir(directory))
        except (FileNotFoundError, NotADirectoryError):
            return []
        return [
            entry.name
            for entry in entries
            if entry.is_file(follow_symlinks=False) and not _is_scratch(entry.name)
        ]


__all__ = [
    "MAX_NAME_LENGTH",
    "SCRATCH_PREFIX",
    "SCRATCH_SUFFIX",
    "TOOL_MODE",
    "InvalidCliToolNameError",
    "LocalCliToolsService",
    "validate_tool_name",
]
