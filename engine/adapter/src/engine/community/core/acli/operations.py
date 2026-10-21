"""``AcliOperations`` — one acli install or uninstall at a time, as an operation.

Installing onto NAS can take minutes, far longer than a caller will hold a
request, so every install and uninstall is an **operation**: it gets an id,
runs to its own end whoever is watching, and can be read back by that id.

**One slot, no queue.** acli cannot run two changes at once, so a container
runs at most one operation. A new request while one is active is refused with
:class:`OperationBusyError` naming the active operation; the caller decides
what to do, nothing waits in line.

**Cancel reverts.** Cancelling kills acli's process group and puts the tool
back the way the operation found it: an install that landed is uninstalled,
an uninstall that removed the tool reinstalls it.

**Every operation is a file**, ``<acli home>/operations/<id>.json`` on the
persisted volume, rewritten (temp file, then rename) on each change. A file
still ``running`` or ``cancelling`` when the engine starts belongs to an
operation the restart cut short: it becomes ``interrupted`` and its recorded
acli process group is killed if it is still alive.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from engine.community.core.acli.runner import AcliError, AcliRunner, validate_name

log = logging.getLogger("engine.acli")

#: The whole operation, acli runs and all. Above it the operation fails with
#: ``timeout`` and its process group is killed.
OPERATION_TIMEOUT_SECONDS = 1800.0
RETAIN_SECONDS = 24 * 3600
RETAIN_COUNT = 200

INSTALL = "install"
UNINSTALL = "uninstall"
ACTIVE_STATES = frozenset({"running", "cancelling"})

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
OPERATIONS_DIR_NAME = "operations"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(slots=True)
class AcliOperation:
    id: str
    action: str
    name: str
    state: str
    started_at: str
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    #: The live acli process group, so a restarted engine can kill it.
    pgid: int | None = None
    #: Whether the tool was installed when the operation started; what a
    #: cancel restores. ``None`` until the operation has looked.
    was_installed: bool | None = None
    _done: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    def public(self) -> dict[str, Any]:
        """What callers see: everything but the engine's own bookkeeping."""
        return {
            "id": self.id, "action": self.action, "name": self.name, "state": self.state,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "result": self.result, "error": self.error,
        }

    def record(self) -> dict[str, Any]:
        return {**self.public(), "pgid": self.pgid, "was_installed": self.was_installed}

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> AcliOperation:
        op = cls(**{k: data[k] for k in _RECORD_FIELDS if k in data})
        if not op.active:
            op._done.set()
        return op


_RECORD_FIELDS = (
    "id", "action", "name", "state", "started_at", "finished_at",
    "result", "error", "pgid", "was_installed",
)


class OperationBusyError(Exception):
    """Another operation holds the container's slot."""

    def __init__(self, operation: AcliOperation) -> None:
        super().__init__(f"acli is busy: {operation.action} {operation.name}")
        self.operation = operation


class OperationNotActiveError(Exception):
    """Nothing of that action and name is running, so there is nothing to cancel."""


class OperationStore:
    """One JSON file per operation, written whole or not at all."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def save(self, op: AcliOperation) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._dir / f".{op.id}.tmp"
        tmp.write_text(json.dumps(op.record()), encoding="utf-8")
        os.replace(tmp, self._dir / f"{op.id}.json")

    def load(self, op_id: str) -> AcliOperation | None:
        try:
            data = json.loads((self._dir / f"{op_id}.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError) as error:
            log.warning("[acli] unreadable operation %s: %s", op_id, error)
            return None
        try:
            return AcliOperation.from_record(data)
        except TypeError as error:
            log.warning("[acli] malformed operation %s: %s", op_id, error)
            return None

    def files(self) -> list[Path]:
        try:
            return [p for p in self._dir.iterdir() if p.suffix == ".json"]
        except FileNotFoundError:
            return []
        except OSError as error:
            log.warning("[acli] cannot list operations: %s", error)
            return []

    def prune(self, *, keep: str | None = None) -> None:
        """Drop operations older than a day, and all but the newest ones."""
        try:
            dated = sorted(
                ((p.stat().st_mtime, p) for p in self.files()), reverse=True
            )
        except OSError as error:
            log.warning("[acli] cannot list operations: %s", error)
            return
        cutoff = time.time() - RETAIN_SECONDS
        for index, (mtime, path) in enumerate(dated):
            if path.stem == keep or (index < RETAIN_COUNT and mtime >= cutoff):
                continue
            try:
                path.unlink()
            except OSError as error:
                log.warning("[acli] cannot prune %s: %s", path.name, error)


class AcliOperations:
    """The container's single acli operation slot."""

    def __init__(self, runner: AcliRunner, store: OperationStore | None = None) -> None:
        self._runner = runner
        self._store = store or OperationStore(runner.home / OPERATIONS_DIR_NAME)
        self._active: AcliOperation | None = None
        self._inner: asyncio.Task | None = None
        self._driver: asyncio.Task | None = None
        # Recently finished operations, so a poll does not read the volume.
        self._recent: OrderedDict[str, AcliOperation] = OrderedDict()
        self._recovered = False

    # ── public ───────────────────────────────────────────────────────────

    def submit(self, action: str, name: str) -> AcliOperation:
        """Start a new operation, or refuse with the one that is active.

        Atomic, and it has to be: two submits racing must not both pass the
        busy check. It is a plain function with no ``await`` between the check
        and taking the slot (``self._active = op``), and every caller runs on
        the engine's one event loop, so no other submit can run in between.
        The record write in between is synchronous for the same reason.
        Keep it that way: an ``await`` added before the slot is taken would
        need an ``asyncio.Lock`` around both.
        """
        if action not in (INSTALL, UNINSTALL):
            raise ValueError(f"unknown acli action {action!r}")
        validate_name(name)
        self._recover()
        if self._active is not None and self._active.active:
            raise OperationBusyError(self._active)
        op = AcliOperation(
            id=uuid.uuid4().hex, action=action, name=name, state="running", started_at=_now()
        )
        self._store.prune(keep=None)
        try:
            self._store.save(op)
        except OSError as error:
            log.warning("[acli] cannot record operation: %s", error)
            raise AcliError(f"{action}_failed", "cannot record the acli operation") from error
        self._active = op
        self._driver = asyncio.create_task(self._drive(op))
        log.info("[acli] operation=%s %s %s started", op.id, action, name)
        return op

    def get(self, op_id: str) -> AcliOperation | None:
        if not isinstance(op_id, str) or not _ID_RE.fullmatch(op_id):
            return None
        self._recover()
        if self._active is not None and self._active.id == op_id:
            return self._active
        return self._recent.get(op_id) or self._store.load(op_id)

    def cancel(self, action: str, name: str) -> AcliOperation:
        """Stop the active ``action`` of ``name`` and revert it."""
        self._recover()
        op = self._active
        if op is None or not op.active or op.action != action or op.name != name:
            raise OperationNotActiveError(f"no {action} of {name} is running")
        if op.state == "running":
            op.state = "cancelling"
            self._save(op)
            log.info("[acli] operation=%s cancelling", op.id)
            if self._inner is not None:
                self._inner.cancel()
        return op

    @staticmethod
    async def wait(op: AcliOperation, timeout: float) -> AcliOperation:
        """``op`` once it ends, or as it is after ``timeout`` seconds."""
        if op.active:
            try:
                await asyncio.wait_for(op._done.wait(), timeout=timeout)
            except TimeoutError:
                pass
        return op

    # ── running ──────────────────────────────────────────────────────────

    async def _drive(self, op: AcliOperation) -> None:
        try:
            await self._run_to_end(op)
        except Exception as error:
            log.exception("[acli] operation=%s crashed", op.id)
            self._fail(op, "bad_output", f"the operation ended unexpectedly: {error}")
        finally:
            op.pgid = None
            op.finished_at = op.finished_at or _now()
            self._save(op)
            self._recent[op.id] = op
            while len(self._recent) > RETAIN_COUNT:
                self._recent.popitem(last=False)
            self._inner = None
            op._done.set()
            log.info("[acli] operation=%s ended state=%s", op.id, op.state)

    async def _run_to_end(self, op: AcliOperation) -> None:
        inner = asyncio.create_task(self._perform(op))
        self._inner = inner
        done, _ = await asyncio.wait({inner}, timeout=OPERATION_TIMEOUT_SECONDS)
        if not done:
            inner.cancel()
            await asyncio.wait({inner})
            if op.state != "cancelling":
                self._fail(op, "timeout", f"acli did not finish within {OPERATION_TIMEOUT_SECONDS:.0f}s")
                return
        if op.state == "cancelling":
            if not inner.cancelled() and inner.exception() is None:
                op.result = inner.result()
            await self._revert(op)
            return
        error = inner.exception()
        if error is None:
            op.result = inner.result()
            op.state = "succeeded"
        elif isinstance(error, AcliError):
            self._fail(op, error.code, error.message)
        else:
            raise error

    async def _perform(self, op: AcliOperation) -> dict[str, Any]:
        spawn = self._spawn_hook(op)
        current = await self._runner.find_installed(
            op.name, failure_code=f"{op.action}_failed", on_spawn=spawn
        )
        op.was_installed = current is not None
        self._save(op)
        if op.action == INSTALL:
            if current is not None:
                return {
                    "name": current.name, "command": current.command,
                    "description": current.description, "version": current.version,
                    "already_installed": True,
                }
            tool = await self._runner.install(op.name, on_spawn=spawn)
            return {
                "name": tool.name, "command": tool.command, "description": tool.description,
                "version": tool.version, "already_installed": False,
            }
        if current is None:
            return {"name": op.name, "uninstalled": False}
        await self._runner.remove(current.name, on_spawn=spawn)
        return {"name": current.name, "uninstalled": True}

    async def _revert(self, op: AcliOperation) -> None:
        """Put the tool back as the operation found it, then end ``cancelled``."""
        op.result = None
        if op.was_installed is None:
            # Cancelled before it looked: nothing was changed.
            op.state = "cancelled"
            return
        spawn = self._spawn_hook(op)
        try:
            current = await asyncio.wait_for(
                self._runner.find_installed(
                    op.name, failure_code="revert_failed", on_spawn=spawn
                ),
                timeout=OPERATION_TIMEOUT_SECONDS,
            )
            if op.action == INSTALL and not op.was_installed and current is not None:
                await asyncio.wait_for(
                    self._runner.remove(current.name, on_spawn=spawn),
                    timeout=OPERATION_TIMEOUT_SECONDS,
                )
            elif op.action == UNINSTALL and op.was_installed and current is None:
                await asyncio.wait_for(
                    self._runner.install(op.name, on_spawn=spawn),
                    timeout=OPERATION_TIMEOUT_SECONDS,
                )
        except (AcliError, TimeoutError) as error:
            reason = error.message if isinstance(error, AcliError) else "timed out"
            expected = "installed" if op.was_installed else "not installed"
            self._fail(
                op, "revert_failed",
                f"cancelled, but restoring {op.name} ({expected} before) failed: {reason}",
            )
            return
        op.state = "cancelled"

    def _fail(self, op: AcliOperation, code: str, message: str) -> None:
        op.state = "failed"
        op.error = {"code": code, "message": message}

    def _spawn_hook(self, op: AcliOperation):
        def record(pid: int) -> None:
            op.pgid = pid
            self._save(op)
        return record

    def _save(self, op: AcliOperation) -> None:
        # The operation itself is in memory; a failed write loses only what a
        # restart could recover, so it is logged, never raised into acli's run.
        try:
            self._store.save(op)
        except OSError as error:
            log.warning("[acli] cannot save operation %s: %s", op.id, error)

    # ── restart ──────────────────────────────────────────────────────────

    def _recover(self) -> None:
        """Close out operations a previous engine process left running."""
        if self._recovered:
            return
        self._recovered = True
        for path in self._store.files():
            op = self._store.load(path.stem)
            if op is None or not op.active:
                continue
            if op.pgid is not None:
                self._kill_stale_group(op.pgid)
            op.state = "interrupted"
            op.error = {"code": "interrupted", "message": "the engine restarted during the operation"}
            op.finished_at = _now()
            op.pgid = None
            self._save(op)
            log.warning("[acli] operation=%s interrupted by a restart", op.id)

    def _kill_stale_group(self, pgid: int) -> None:
        """Kill a group left by an earlier engine, if it is still acli's.

        A pid can be reused after a container restart, so the group is killed
        only when its leader is still the acli executable.
        """
        if pgid <= 1 or pgid == os.getpgrp():
            return
        try:
            argv = Path(f"/proc/{pgid}/cmdline").read_bytes().split(b"\0")[:2]
        except OSError:
            return
        executable = os.path.basename(self._runner.executable).encode()
        if not any(os.path.basename(arg) == executable for arg in argv):
            return
        try:
            os.killpg(pgid, signal.SIGKILL)
            log.warning("[acli] killed leftover acli process group %s", pgid)
        except (ProcessLookupError, PermissionError):
            pass


__all__ = [
    "ACTIVE_STATES",
    "INSTALL",
    "OPERATION_TIMEOUT_SECONDS",
    "UNINSTALL",
    "AcliOperation",
    "AcliOperations",
    "OperationBusyError",
    "OperationNotActiveError",
    "OperationStore",
]
