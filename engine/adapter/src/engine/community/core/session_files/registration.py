"""Descriptor-relative workspace snapshots; no external transfer is involved."""

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
    root: Path, *, session_key: str, workspace_relative_path: str
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
    scope_hash = hash_identifier("workspace_file")
    try:
        build_session_file_relative_path(
            scope_key_hash=scope_hash,
            session_key_hash=session_hash,
            resource_id="validate",
            filename=relative.name,
        )
    except MaterializationSecurityError as exc:
        raise SessionFileError("invalid_workspace_path") from exc

    with _directory(root, (".teamclaw", "session-files"), create=True) as directory:
        temporary = f".register-{uuid.uuid4().hex}.part"
        try:
            with _source(root, relative) as source:
                before = os.fstat(source.fileno())
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                digest = hashlib.sha256()
                total = 0
                with os.fdopen(descriptor, "wb") as target:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                        total += len(chunk)
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                # COSEC: compare the opened inode and re-open the original path to
                # reject mutation, replacement, and ancestor rename during copying.
                with _source(root, relative) as current:
                    if (
                        total != before.st_size
                        or _observation(before)
                        != _observation(os.fstat(source.fileno()))
                        or _observation(before)
                        != _observation(os.fstat(current.fileno()))
                    ):
                        raise SessionFileError("resource_changed")
            content_hash = digest.hexdigest()
            identity = json.dumps([session_key, relative.as_posix(), content_hash])
            resource_id = "workspace_" + hash_identifier(identity)
            target_relative = build_session_file_relative_path(
                scope_key_hash=scope_hash,
                session_key_hash=session_hash,
                resource_id=resource_id,
                filename=relative.name,
            )
            store = _PinnedManifestStore(root, directory)
            # COSEC: the existing thread lock also serializes legacy manifest writers;
            # flock additionally serializes registrations in separate Engine processes.
            with store._lock_for(store.path):
                lock_fd = os.open(
                    ".registration.lock",
                    os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                with os.fdopen(lock_fd, "a") as lock:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                    existing = store.get(resource_id)
                    if existing is not None:
                        if (
                            existing.status != "ready"
                            or existing.session_key_hash != session_hash
                            or existing.relative_path != target_relative.as_posix()
                            or existing.content_hash != content_hash
                        ):
                            raise SessionFileError("resource_changed")
                        with _source(root, target_relative) as snapshot:
                            if (
                                hashlib.file_digest(snapshot, "sha256").hexdigest()
                                != content_hash
                            ):
                                raise SessionFileError("resource_changed")
                        return existing
                    with _directory(
                        root, target_relative.parts[:-1], create=True
                    ) as parent:
                        os.replace(
                            temporary,
                            target_relative.name,
                            src_dir_fd=directory,
                            dst_dir_fd=parent,
                        )
                        try:
                            # COSEC: re-open and verify the published snapshot,
                            # rejecting a replacement before manifest publication.
                            with _source(root, target_relative) as snapshot:
                                observed = os.fstat(snapshot.fileno())
                                if (
                                    observed.st_size != total
                                    or hashlib.file_digest(snapshot, "sha256").hexdigest()
                                    != content_hash
                                ):
                                    raise SessionFileError("resource_changed")
                            entry = ManifestEntry(
                                resource_id=resource_id,
                                transfer_id="",
                                task_id="",
                                task_version=1,
                                scope_key_hash=scope_hash,
                                session_key_hash=session_hash,
                                filename=relative.name,
                                relative_path=target_relative.as_posix(),
                                canonical_bot_absolute_path=str(root / target_relative),
                                size_bytes=total,
                                content_hash=content_hash,
                                status="ready",
                                observed_size=observed.st_size,
                                observed_mtime_ns=observed.st_mtime_ns,
                                observed_inode=observed.st_ino,
                                uploaded_at=datetime.now(UTC),
                                source_kind="workspace_file",
                            )
                            _check_directory(root, target_relative.parts[:-1], parent)
                            store.upsert(entry)
                            return entry
                        except Exception:
                            os.unlink(target_relative.name, dir_fd=parent)
                            raise
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
