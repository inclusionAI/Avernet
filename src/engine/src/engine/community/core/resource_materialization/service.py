"""Materialize BaaS transfers into the controlled Bot workspace."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
import threading
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar
from urllib.parse import quote

from engine.community.core.resource_materialization.models import (
    ManifestEntry,
    MaterializationRequest,
    MaterializationResult,
    MaterializedContent,
)
from engine.community.plugin_api.resource_materialization import (
    BaasMaterializationClient,
    BackendMaterializationCallbackClient,
)
from engine.community.plugin_api.workspace_root import workspace_root_strict

log = logging.getLogger("engine.resource_materialization")
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class MaterializationSecurityError(ValueError):
    """The requested workspace path escaped or violated the controlled root."""


class ResourceNotMaterializedError(ValueError):
    """The requested resource has no readable ready workspace file."""


class ManifestStore:
    """Atomic JSON manifest stored inside the Bot workspace."""

    _locks: ClassVar[dict[Path, Any]] = {}
    _locks_guard: ClassVar[Any] = threading.Lock()

    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / ".teamclaw/session-files/.manifest.json"

    @classmethod
    def _lock_for(cls, path: Path):
        with cls._locks_guard:
            lock = cls._locks.get(path)
            if lock is None:
                lock = threading.RLock()
                cls._locks[path] = lock
            return lock

    def _read_locked(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "resources": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("resource manifest is unreadable") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("resources"), dict):
            raise RuntimeError("resource manifest has invalid shape")
        return raw

    def get(self, resource_id: str) -> ManifestEntry | None:
        with self._lock_for(self.path):
            value = self._read_locked()["resources"].get(resource_id)
            return ManifestEntry.model_validate(value) if value is not None else None

    def list_entries(self) -> list[ManifestEntry]:
        with self._lock_for(self.path):
            values = self._read_locked()["resources"].values()
            return [ManifestEntry.model_validate(value) for value in values]

    def upsert(self, entry: ManifestEntry) -> None:
        with self._lock_for(self.path):
            payload = self._read_locked()
            payload["version"] = 1
            payload["resources"][entry.resource_id] = entry.model_dump(mode="json")
            self._write_locked(payload)

    def remove(self, resource_id: str) -> ManifestEntry | None:
        with self._lock_for(self.path):
            payload = self._read_locked()
            value = payload["resources"].pop(resource_id, None)
            if value is None:
                return None
            payload["version"] = 1
            self._write_locked(payload)
            return ManifestEntry.model_validate(value)

    def _write_locked(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".manifest-{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, separators=(",", ":"), sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


class ResourceMaterializationService:
    """Pull, validate, atomically publish, manifest, then callback."""

    def __init__(
        self,
        *,
        pull_client: BaasMaterializationClient,
        callback_client: BackendMaterializationCallbackClient,
        workspace_root_provider: Callable[[], Path | None] = workspace_root_strict,
    ) -> None:
        self._pull_client = pull_client
        self._callback_client = callback_client
        self._workspace_root_provider = workspace_root_provider
        self._lock = asyncio.Lock()

    @property
    def manifest_store(self) -> ManifestStore:
        return ManifestStore(self._workspace_root())

    def open_content(
        self,
        *,
        resource_id: str,
        disposition: str,
    ) -> MaterializedContent:
        """Resolve a ready manifest entry to a controlled workspace file."""
        if disposition not in {"inline", "attachment"}:
            raise ValueError("invalid_disposition")
        root = self._workspace_root()
        entry = ManifestStore(root).get(resource_id)
        if entry is None or entry.status != "ready":
            raise ResourceNotMaterializedError("resource_not_materialized")
        relative = Path(entry.relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ResourceNotMaterializedError("resource_not_materialized")
        target = root / relative
        try:
            # COSEC: resolve symlinks before checking parents so a manifest file
            # can never disclose a sibling or host path through the content API.
            self._assert_contained(root, target)
            canonical = target.resolve(strict=True)
            if not canonical.is_file() or canonical.stat().st_size != entry.size_bytes:
                raise ResourceNotMaterializedError("resource_not_materialized")
            if self._sha256(canonical) != entry.content_hash:
                raise ResourceNotMaterializedError("resource_not_materialized")
        except (OSError, MaterializationSecurityError) as exc:
            raise ResourceNotMaterializedError("resource_not_materialized") from exc
        media_type = mimetypes.guess_type(entry.filename)[0] or "application/octet-stream"
        content_disposition = "{}; filename*=UTF-8''{}".format(
            disposition,
            quote(entry.filename, safe=""),
        )
        log.info(
            "engine.resource_content.open resource_id=%s disposition=%s size_bytes=%s",
            resource_id,
            disposition,
            entry.size_bytes,
        )
        return MaterializedContent(
            path=canonical,
            filename=entry.filename,
            media_type=media_type,
            content_disposition=content_disposition,
            size_bytes=entry.size_bytes,
        )

    async def materialize(
        self,
        request: MaterializationRequest,
    ) -> MaterializationResult:
        async with self._lock:
            try:
                result = await self._materialize_locked(request)
            except MaterializationSecurityError as exc:
                log.warning(
                    "engine.resource_materialize.path.reject resource_id=%s task_version=%s error_type=%s",
                    request.resource_id,
                    request.task_version,
                    type(exc).__name__,
                )
                result = self._failure_result(request, "invalid_device_path")
            except Exception as exc:
                log.warning(
                    "engine.resource_materialize.pull.fail resource_id=%s task_version=%s error_type=%s",
                    request.resource_id,
                    request.task_version,
                    type(exc).__name__,
                )
                result = self._failure_result(request, "pull_failed")
        # Callback transport errors must not be reclassified as pull failures.
        await self._report(result)
        return result

    async def _materialize_locked(
        self,
        request: MaterializationRequest,
    ) -> MaterializationResult:
        root = self._workspace_root()
        relative, target = self._target(root, request)
        store = ManifestStore(root)
        current = store.get(request.resource_id)
        if current is not None and current.task_version > request.task_version:
            return self._failure_result(request, "stale_task_version")
        if (
            current is not None
            and current.task_version == request.task_version
            and current.task_id == request.task_id
            and current.status == "ready"
            and await self._entry_file_is_valid(root, current)
        ):
            log.info(
                "engine.resource_materialize.idempotent resource_id=%s task_version=%s",
                request.resource_id,
                request.task_version,
            )
            return self._success_from_entry(current)

        target.parent.mkdir(parents=True, exist_ok=True)
        # COSEC: resolve the final parent after mkdir and enforce containment;
        # this rejects pre-existing symlinks that redirect writes outside Bot root.
        self._assert_contained(root, target)
        temporary = target.with_name(f".{target.name}.part-{uuid.uuid4().hex}")
        log.info(
            "engine.resource_materialize.pull.start resource_id=%s task_version=%s path_hash=%s",
            request.resource_id,
            request.task_version,
            self._path_hash(target),
        )
        try:
            await self._pull_client.pull(request, temporary)
            if not temporary.is_file():
                return self._failure_result(request, "pull_missing_file")
            actual_size = temporary.stat().st_size
            actual_hash = await asyncio.to_thread(self._sha256, temporary)
            if request.size_bytes is not None and actual_size != request.size_bytes:
                return self._failure_result(request, "size_mismatch")
            if request.content_hash is not None and actual_hash != request.content_hash:
                return self._failure_result(request, "hash_mismatch")
            # COSEC: re-resolve immediately before publish so a replaced parent
            # symlink cannot redirect the final atomic rename outside Bot root.
            self._assert_contained(root, target)
            os.replace(temporary, target)
            canonical = target.resolve(strict=True)
            observed = canonical.stat()
            entry = ManifestEntry(
                resource_id=request.resource_id,
                transfer_id=request.transfer_id,
                task_id=request.task_id,
                task_version=request.task_version,
                scope_key_hash=request.scope_key_hash,
                session_key_hash=request.session_key_hash,
                filename=request.filename,
                relative_path=relative.as_posix(),
                canonical_bot_absolute_path=str(canonical),
                size_bytes=actual_size,
                content_hash=actual_hash,
                status="ready",
                observed_size=observed.st_size,
                observed_mtime_ns=observed.st_mtime_ns,
                observed_inode=getattr(observed, "st_ino", None),
            )
            store.upsert(entry)
            log.info(
                "engine.resource_materialize.pull.done resource_id=%s task_version=%s path_hash=%s size_bytes=%s",
                request.resource_id,
                request.task_version,
                self._path_hash(canonical),
                actual_size,
            )
            return self._success_from_entry(entry)
        finally:
            temporary.unlink(missing_ok=True)

    def _workspace_root(self) -> Path:
        value = self._workspace_root_provider()
        if value is None:
            raise MaterializationSecurityError("Bot workspace root is not configured")
        root = Path(value)
        if not root.is_absolute():
            raise MaterializationSecurityError("Bot workspace root must be absolute")
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve(strict=True)

    def _target(
        self,
        root: Path,
        request: MaterializationRequest,
    ) -> tuple[Path, Path]:
        segments = (
            request.scope_key_hash,
            request.session_key_hash,
            request.resource_id,
            request.filename,
        )
        if any(not _SAFE_SEGMENT.fullmatch(segment) for segment in segments):
            raise MaterializationSecurityError("invalid controlled path segment")
        supplied_path = request.workspace_relative_path or request.device_path
        if supplied_path is None:
            raise MaterializationSecurityError("controlled path is missing")
        supplied = PurePosixPath(supplied_path.replace("\\", "/"))
        if ".." in supplied.parts:
            raise MaterializationSecurityError("device_path traversal is forbidden")
        expected_suffix = (
            ".teamclaw",
            "session-files",
            request.scope_key_hash,
            request.session_key_hash,
            request.resource_id,
            request.filename,
        )
        if request.transfer_api_version == "session_v2":
            if tuple(supplied.parts) != expected_suffix:
                raise MaterializationSecurityError("workspace path does not match resource scope")
        elif tuple(supplied.parts[-len(expected_suffix):]) != expected_suffix:
            raise MaterializationSecurityError("device_path does not match resource scope")
        relative = Path(*expected_suffix)
        target = root / relative
        self._assert_contained(root, target)
        return relative, target

    @staticmethod
    def _assert_contained(root: Path, target: Path) -> None:
        # COSEC: Path.resolve follows symlinks before the parent relation check;
        # string-prefix checks are insufficient for sibling-prefix and symlink escapes.
        resolved_root = root.resolve(strict=True)
        resolved_target = target.resolve(strict=False)
        if resolved_target != resolved_root and resolved_root not in resolved_target.parents:
            raise MaterializationSecurityError("workspace path escapes Bot root")

    async def _entry_file_is_valid(self, root: Path, entry: ManifestEntry) -> bool:
        relative = Path(entry.relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            return False
        target = root / relative
        try:
            self._assert_contained(root, target)
            if not target.is_file() or target.stat().st_size != entry.size_bytes:
                return False
            return await asyncio.to_thread(self._sha256, target) == entry.content_hash
        except (OSError, MaterializationSecurityError):
            return False

    @staticmethod
    def _failure_result(
        request: MaterializationRequest,
        error_code: str,
    ) -> MaterializationResult:
        return MaterializationResult(
            resource_id=request.resource_id,
            transfer_id=request.transfer_id,
            task_id=request.task_id,
            task_version=request.task_version,
            ready=False,
            error_code=error_code,
        )

    async def _report(self, result: MaterializationResult) -> None:
        last_error: Exception | None = None
        log.info(
            "engine.resource_materialize.callback.start resource_id=%s task_version=%s ready=%s",
            result.resource_id,
            result.task_version,
            result.ready,
        )
        for attempt in range(1, 4):
            try:
                await self._callback_client.report(result)
                log.info(
                    "engine.resource_materialize.callback.done resource_id=%s task_version=%s attempt=%s",
                    result.resource_id,
                    result.task_version,
                    attempt,
                )
                return
            except Exception as exc:  # callback is idempotent by task version
                last_error = exc
                log.warning(
                    "engine.resource_materialize.callback.retry resource_id=%s task_version=%s attempt=%s error_type=%s",
                    result.resource_id,
                    result.task_version,
                    attempt,
                    type(exc).__name__,
                )
                await asyncio.sleep(0)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _success_from_entry(entry: ManifestEntry) -> MaterializationResult:
        return MaterializationResult(
            resource_id=entry.resource_id,
            transfer_id=entry.transfer_id,
            task_id=entry.task_id,
            task_version=entry.task_version,
            ready=True,
            canonical_bot_absolute_path=entry.canonical_bot_absolute_path,
            relative_path=entry.relative_path,
            size_bytes=entry.size_bytes,
            content_hash=entry.content_hash,
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _path_hash(path: Path) -> str:
        return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
