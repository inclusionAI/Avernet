"""Descriptor-relative live workspace registration and coherent observations."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import uuid
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from engine.community.core.resource_materialization.models import (
    ManifestEntry,
    hash_identifier,
)
from engine.community.core.resource_materialization.service import (
    ManifestStore,
    MaterializationSecurityError,
    build_session_file_relative_path,
)
from engine.community.core.session_files.models import SessionFileError

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


@contextmanager
def _directory(root: Path, parts: tuple[str, ...], *, create: bool = False):
    # COSEC: pin every component; never follow a symlink after validating a path.
    with ExitStack() as stack:
        descriptor = os.open(root, _DIRECTORY_FLAGS)
        stack.callback(os.close, descriptor)
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            descriptor = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            stack.callback(os.close, descriptor)
        yield descriptor


@contextmanager
def _source(root: Path, relative: Path):
    with _directory(root, relative.parts[:-1]) as parent:
        descriptor = os.open(relative.name, _READ_FLAGS, dir_fd=parent)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise SessionFileError("invalid_workspace_file")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                yield stream
        finally:
            os.close(descriptor)


def _observation(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _check_directory(root: Path, parts: tuple[str, ...], descriptor: int) -> None:
    with _directory(root, parts) as current:
        if (
            _observation(os.fstat(current))[:2]
            != _observation(os.fstat(descriptor))[:2]
        ):
            raise SessionFileError("resource_changed")


class _PinnedManifestStore(ManifestStore):
    """Reuse the store's merge/lock policy with symlink-safe filesystem I/O."""

    def __init__(self, root: Path, descriptor: int) -> None:
        super().__init__(root)
        self._descriptor = descriptor

    def _read_locked(self) -> dict:
        try:
            descriptor = os.open(self.path.name, _READ_FLAGS, dir_fd=self._descriptor)
        except FileNotFoundError:
            return {"version": 1, "resources": {}}
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise RuntimeError("resource manifest is not a file")
            payload = json.load(stream)
        if not isinstance(payload, dict) or not isinstance(
            payload.get("resources"), dict
        ):
            raise RuntimeError("resource manifest has invalid shape")
        return payload

    def _write_locked(self, payload: dict) -> None:
        temporary = f".manifest-{uuid.uuid4().hex}.tmp"
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=self._descriptor,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, separators=(",", ":"), sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            _check_directory(
                self.root, (".teamclaw", "session-files"), self._descriptor
            )
            os.replace(
                temporary,
                self.path.name,
                src_dir_fd=self._descriptor,
                dst_dir_fd=self._descriptor,
            )
        finally:
            try:
                os.unlink(temporary, dir_fd=self._descriptor)
            except FileNotFoundError:
                pass


def register_workspace_file(
    root: Path, *, session_key: str, workspace_relative_path: str,
    creator_name: str | None = None,
) -> ManifestEntry:
    relative = Path(workspace_relative_path)
    # COSEC: only ordinary workspace sources; internal snapshots are never sources.
    if (
        not session_key
        or len(session_key) > 2048
        or not workspace_relative_path
        or relative.is_absolute()
        or ".." in relative.parts
        or not relative.parts
        or relative.parts[0] == ".teamclaw"
        or "\\" in workspace_relative_path
        or any(not character.isprintable() for character in workspace_relative_path)
    ):
        raise SessionFileError("invalid_workspace_path")
    session_hash = hash_identifier(session_key)
    scope_hash = hash_identifier("workspace_live")
    try:
        build_session_file_relative_path(
            scope_key_hash=scope_hash,
            session_key_hash=session_hash,
            resource_id="validate",
            filename=relative.name,
        )
    except MaterializationSecurityError as exc:
        raise SessionFileError("invalid_workspace_path") from exc

    observed, content_hash = observe_workspace_file(root, relative)
    identity = json.dumps([session_key, relative.as_posix()])
    resource_id = "workspace_live_" + hash_identifier(identity)
    with _directory(root, (".teamclaw", "session-files"), create=True) as directory:
        store = _PinnedManifestStore(root, directory)
        # COSEC: serialize manifest merges across threads and Engine processes.
        with store._lock_for(store.path):
            # Use one exclusive creator; contenders open the existing no-follow
            # inode without another O_CREAT lookup. Missing/deleted directories
            # still fail closed.
            try:
                lock_fd = os.open(
                    ".registration.lock",
                    os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                    0o600, dir_fd=directory,
                )
            except FileExistsError:
                lock_fd = os.open(
                    ".registration.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=directory,
                )
            with os.fdopen(lock_fd, "a") as lock:
                # COSEC: a preexisting lock must be an ordinary no-follow file.
                if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                    raise SessionFileError("registration_failed")
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                existing = store.get(resource_id)
                registered_at = existing.uploaded_at if existing else datetime.now(UTC)
                entry = ManifestEntry(
                    resource_id=resource_id, transfer_id="", task_id="", task_version=1,
                    scope_key_hash=scope_hash, session_key_hash=session_hash,
                    filename=relative.name, relative_path=relative.as_posix(),
                    canonical_bot_absolute_path=str(root / relative),
                    size_bytes=observed.st_size, content_hash=content_hash, status="ready",
                    observed_size=observed.st_size, observed_mtime_ns=observed.st_mtime_ns,
                    observed_inode=observed.st_ino,
                    uploaded_at=registered_at,
                    creator_type=existing.creator_type if existing else "bot",
                    creator_name=existing.creator_name if existing else creator_name,
                    created_at=(existing.created_at or existing.uploaded_at) if existing else registered_at,
                    source_kind="workspace_live",
                )
                # Registration observations must not overwrite the version paired
                # with an existing external transfer. Access always observes anew.
                if existing is not None and existing.transfer_id:
                    entry = entry.model_copy(update={
                        "transfer_id": existing.transfer_id,
                        "baas_tenant": existing.baas_tenant,
                        "content_hash": existing.content_hash,
                        "size_bytes": existing.size_bytes,
                    })
                store.upsert(entry)
                return entry.model_copy(update={
                    "size_bytes": observed.st_size, "content_hash": content_hash,
                })


def observe_workspace_file(
    root: Path,
    relative: Path,
    target: BinaryIO | None = None,
    *,
    max_bytes: int | None = None,
) -> tuple[os.stat_result, str]:
    """Read one pinned ordinary file; reject mutations and path swaps in flight.

    An optional caller-owned stream receives the observed bytes. Registration
    and metadata observations do not create any persistent content copy.
    """
    # COSEC: defend manifest-sourced paths as well as registration input.
    if (
        relative.is_absolute() or ".." in relative.parts or not relative.parts
        or relative.parts[0] == ".teamclaw" or "\\" in str(relative)
    ):
        raise SessionFileError("resource_missing")
    with _source(root, relative) as source:
        before = os.fstat(source.fileno())
        if max_bytes is not None and before.st_size > max_bytes:
            raise SessionFileError("resource_preview_too_large")
        digest = hashlib.sha256()
        total = 0
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise SessionFileError("resource_changed")
            digest.update(chunk)
            if target is not None:
                target.write(chunk)
        # COSEC: verify inode metadata and the entire original path after reading;
        # an atomic replacement is accepted between operations, never mid-read.
        with _source(root, relative) as current:
            if (
                total != before.st_size
                or _observation(before) != _observation(os.fstat(source.fileno()))
                or _observation(before) != _observation(os.fstat(current.fileno()))
            ):
                raise SessionFileError("resource_changed")
        return before, digest.hexdigest()
