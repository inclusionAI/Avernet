"""Serve ready session files directly from the controlled Engine workspace."""

from __future__ import annotations

import errno
import hashlib
import logging
import mimetypes
import time
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from engine.community.core.resource_materialization.models import (
    ManifestEntry,
    MaterializedContent,
    hash_identifier,
)
from engine.community.core.resource_materialization.service import ManifestStore
from engine.community.core.session_files.models import (
    MAX_DIRECT_CONTENT_BYTES,
    SessionFileError,
    SessionFileExportSource,
    SessionFileView,
)
from engine.community.core.session_files.registration import (
    observe_workspace_file, register_workspace_file,
)
from engine.community.plugin_api.workspace_root import workspace_root_strict
from engine.community.plugin_api.runtime_identity import runtime_bot_name

log = logging.getLogger("engine.session_files")


class SessionFileService:
    """Manifest-only list, content, and deletion operations for one session."""

    def __init__(
        self,
        *,
        workspace_root_provider: Callable[[], Path | None] = workspace_root_strict,
        default_baas_tenant: str | None = None,
        bot_name: str | None = None,
        bot_name_provider: Callable[[], str | None] = runtime_bot_name,
    ) -> None:
        self._workspace_root_provider = workspace_root_provider
        self._default_baas_tenant = default_baas_tenant
        configured_name = bot_name if bot_name is not None else bot_name_provider()
        self._bot_name = (configured_name.strip() or None) if configured_name is not None else None

    def register_file(
        self, *, session_key: str, workspace_relative_path: str
    ) -> SessionFileView:
        """Register a live workspace association; keep the original source intact."""
        started = time.monotonic()
        fields = {
            "system": "engine", "operation": "session_files.register",
            "sessionKey": session_key, "workspace_relative_path": workspace_relative_path,
        }
        log.info(
            "engine.session_files.register.request system=engine sessionKey=%r workspace_relative_path=%r status=request",
            session_key, workspace_relative_path, extra={**fields, "status": "request"},
        )
        try:
            entry = register_workspace_file(
                self._workspace_root(), session_key=session_key,
                workspace_relative_path=workspace_relative_path,
                creator_name=self._bot_name,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            code = "registration_failed"
            if isinstance(exc, OSError) and exc.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
                code = "invalid_workspace_path"
            error = exc if isinstance(exc, SessionFileError) else SessionFileError(code)
            elapsed_ms = round((time.monotonic() - started) * 1000, 3)
            log.warning(
                "engine.session_files.register.failure system=engine sessionKey=%r workspace_relative_path=%r status=failed error_code=%s elapsed_ms=%s",
                session_key, workspace_relative_path, str(error), elapsed_ms,
                extra={**fields, "status": "failed", "error_code": str(error), "elapsed_ms": elapsed_ms},
            )
            if error is exc:
                raise
            raise error from exc
        elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        log.info(
            "engine.session_files.register.success system=engine sessionKey=%r workspace_relative_path=%r resource_id=%s status=ready size_bytes=%s elapsed_ms=%s",
            session_key, workspace_relative_path, entry.resource_id, entry.size_bytes, elapsed_ms,
            extra={**fields, "status": "ready", "resource_id": entry.resource_id,
                   "creator_type": entry.creator_type,
                   "creator_name_present": entry.creator_name is not None,
                   "created_at": self._format_uploaded_at(entry.created_at or entry.uploaded_at),
                   "size_bytes": entry.size_bytes, "elapsed_ms": elapsed_ms},
        )
        return SessionFileView(
            resource_id=entry.resource_id, display_name=entry.filename,
            size_bytes=entry.size_bytes, availability="ready",
            uploaded_at=self._format_uploaded_at(entry.uploaded_at),
            creator_type=entry.creator_type,
            creator_name=entry.creator_name,
            created_at=self._format_uploaded_at(entry.created_at or entry.uploaded_at),
        )

    def list_files(self, *, session_key: str) -> list[SessionFileView]:
        root = self._workspace_root()
        session_hash = hash_identifier(session_key)
        try:
            entries = ManifestStore(root).list_entries()
        except RuntimeError as exc:
            raise SessionFileError("manifest_unavailable") from exc
        files = []
        for entry in entries:
            if entry.status != "ready" or entry.session_key_hash != session_hash:
                continue
            if entry.source_kind == "workspace_live":
                availability, _, size_bytes, _ = self._current_file(root, entry)
            else:
                availability, _ = self._inspect(root, entry, force_hash=False)
                size_bytes = entry.size_bytes
            files.append(SessionFileView(
                resource_id=entry.resource_id,
                display_name=entry.filename,
                size_bytes=size_bytes,
                availability=availability,
                uploaded_at=self._format_uploaded_at(entry.uploaded_at),
                creator_type=entry.creator_type,
                creator_name=entry.creator_name,
                created_at=self._format_uploaded_at(entry.created_at or entry.uploaded_at),
            ))
        log.info(
            "engine.session_files.list session_key_hash=%s file_count=%s upload_time_count=%s",
            session_hash[:16],
            len(files),
            sum(file.uploaded_at is not None for file in files),
        )
        return files

    @staticmethod
    def _format_uploaded_at(uploaded_at: datetime | None) -> str | None:
        if uploaded_at is None:
            return None
        if uploaded_at.tzinfo is None:
            uploaded_at = uploaded_at.replace(tzinfo=UTC)
        return uploaded_at.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def open_content(
        self,
        *,
        session_key: str,
        resource_id: str,
        disposition: str,
    ) -> MaterializedContent:
        if disposition not in {"inline", "attachment"}:
            raise SessionFileError("invalid_disposition")
        root, entry = self._entry_for_session(session_key, resource_id)
        stream = None
        size_bytes = entry.size_bytes
        if entry.source_kind == "workspace_live":
            # COSEC: anonymous operation-local storage pins response bytes through
            # actual consumption; the HTTP adapter must never reopen the source.
            stream = tempfile.TemporaryFile(mode="w+b")
            try:
                observed, _ = observe_workspace_file(
                    root, Path(entry.relative_path), stream, max_bytes=MAX_DIRECT_CONTENT_BYTES
                )
                size_bytes = observed.st_size
                canonical = root / entry.relative_path
                stream.seek(0)
            except (OSError, SessionFileError) as exc:
                stream.close()
                code = str(exc) if str(exc) in {"resource_changed", "resource_preview_too_large"} else "resource_missing"
                log.info("engine.session_files.live.unavailable resource_id=%s error_code=%s", resource_id, code)
                raise SessionFileError(code) from None
        else:
            availability, canonical = self._inspect(root, entry, force_hash=True)
            if availability != "ready" or canonical is None:
                raise SessionFileError(f"resource_{availability}")
        media_type = (
            mimetypes.guess_type(entry.filename)[0] or "application/octet-stream"
        )
        content_disposition = "{}; filename*=UTF-8''{}".format(
            disposition,
            quote(entry.filename, safe=""),
        )
        log.info(
            "engine.session_files.content.open resource_id=%s disposition=%s size_bytes=%s",
            resource_id,
            disposition,
            size_bytes,
        )
        return MaterializedContent(
            path=canonical,
            filename=entry.filename,
            media_type=media_type,
            content_disposition=content_disposition,
            size_bytes=size_bytes,
            stream=stream,
        )

    def delete_file(self, *, session_key: str, resource_id: str) -> None:
        root, entry = self._entry_for_session(session_key, resource_id)
        removed = ManifestStore(root).remove(resource_id)
        if removed is None:
            raise SessionFileError("resource_not_found")
        if entry.source_kind != "workspace_live":
            self._remove_local_file(root, entry)
        log.info(
            "engine.session_files.delete resource_id=%s session_key_hash=%s",
            resource_id,
            hash_identifier(session_key)[:16],
        )

    def get_file_size(self, *, session_key: str, resource_id: str) -> int:
        """Inspect delivery size independently of any external BaaS capability."""
        root, entry = self._entry_for_session(session_key, resource_id)
        availability, _, size_bytes, _ = self._current_file(root, entry)
        if availability != "ready":
            raise SessionFileError(f"resource_{availability}")
        log.info(
            "engine.session_files.delivery.size sessionKey=%r resource_id=%s size_bytes=%s",
            session_key, resource_id, size_bytes,
        )
        return size_bytes

    def prepare_export_source(
        self,
        *,
        session_key: str,
        resource_id: str,
    ) -> SessionFileExportSource:
        """Resolve the current controlled file for a large attachment request."""
        root, entry = self._entry_for_session(session_key, resource_id)
        availability, canonical, size_bytes, content_hash = self._current_file(
            root, entry
        )
        if availability != "ready" or canonical is None:
            raise SessionFileError(f"resource_{availability}")
        requires_upload = (
            (entry.source_kind != "baas_session_file" and not (entry.source_kind == "workspace_live" and entry.transfer_id))
            or size_bytes != entry.size_bytes
            or content_hash != entry.content_hash
        )
        log.info(
            "engine.session_files.export.source resource_id=%s size_bytes=%s requires_upload=%s",
            resource_id,
            size_bytes,
            requires_upload,
        )
        tenant = entry.baas_tenant or self._default_baas_tenant
        if not tenant:
            raise SessionFileError("resource_export_tenant_unavailable")
        return SessionFileExportSource(
            resource_id=entry.resource_id,
            session_key_hash=entry.session_key_hash,
            filename=entry.filename,
            size_bytes=size_bytes,
            content_hash=content_hash,
            canonical_path=canonical,
            tenant=tenant,
            transfer_id=entry.transfer_id,
            requires_upload=requires_upload,
        )

    def revalidate_export_source(
        self,
        source: SessionFileExportSource,
    ) -> None:
        """Ensure an asynchronous export did not race a file or manifest change."""
        root = self._workspace_root()
        try:
            entry = ManifestStore(root).get(source.resource_id)
        except RuntimeError as exc:
            raise SessionFileError("manifest_unavailable") from exc
        if entry is None or entry.status != "ready":
            raise SessionFileError("resource_missing")
        if entry.session_key_hash != source.session_key_hash:
            raise SessionFileError("resource_changed")
        availability, canonical, size_bytes, content_hash = self._current_file(
            root, entry
        )
        if (
            availability != "ready"
            or canonical != source.canonical_path
            or size_bytes != source.size_bytes
            or content_hash != source.content_hash
        ):
            raise SessionFileError(
                f"resource_{availability}" if availability != "ready" else "resource_changed"
            )

    def create_export_snapshot(self, source: SessionFileExportSource) -> Path:
        """Copy a verified current file before an Engine-originated upload."""
        self.revalidate_export_source(source)
        root, entry = self._entry_for_session_hash(source.resource_id, source.session_key_hash)
        # COSEC: use a private operation-local transfer file, never a workspace
        # directory whose components can be replaced with symlinks by the Bot.
        descriptor, name = tempfile.mkstemp(prefix="session-file-export-", suffix=".part")
        snapshot = Path(name)
        digest = hashlib.sha256()
        total = 0
        try:
            with open(descriptor, "wb") as target:
                if entry.source_kind == "workspace_live":
                    try:
                        observed, content_hash = observe_workspace_file(
                            root, Path(entry.relative_path), target
                        )
                    except (OSError, SessionFileError) as exc:
                        if str(exc) == "resource_changed":
                            raise
                        raise SessionFileError("resource_missing") from None
                    total = observed.st_size
                else:
                    with source.canonical_path.open("rb") as source_stream:
                        for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                            total += len(chunk)
                            digest.update(chunk)
                            target.write(chunk)
                    content_hash = digest.hexdigest()
            if total != source.size_bytes or content_hash != source.content_hash:
                raise SessionFileError("resource_changed")
            self.revalidate_export_source(source)
            return snapshot
        except BaseException:
            snapshot.unlink(missing_ok=True)
            raise

    def promote_export_source(
        self,
        source: SessionFileExportSource,
        *,
        transfer_id: str,
    ) -> SessionFileExportSource:
        """Atomically make a verified re-upload the manifest's current source."""
        self.revalidate_export_source(source)
        root, entry = self._entry_for_session_hash(
            source.resource_id, source.session_key_hash
        )
        if entry.source_kind == "workspace_live":
            try:
                observed, content_hash = observe_workspace_file(root, Path(entry.relative_path))
            except OSError:
                raise SessionFileError("resource_missing") from None
            if observed.st_size != source.size_bytes or content_hash != source.content_hash:
                raise SessionFileError("resource_changed")
        else:
            observed = source.canonical_path.stat()
        updated = ManifestStore(root).replace_export_source(
            resource_id=source.resource_id,
            scope_key_hash=entry.scope_key_hash,
            session_key_hash=source.session_key_hash,
            transfer_id=transfer_id,
            size_bytes=source.size_bytes,
            content_hash=source.content_hash,
            observed_size=observed.st_size,
            observed_mtime_ns=observed.st_mtime_ns,
            observed_inode=getattr(observed, "st_ino", None),
            baas_tenant=source.tenant,
        )
        if updated is None:
            raise SessionFileError("resource_changed")
        tenant = updated.baas_tenant or self._default_baas_tenant
        if not tenant:
            raise SessionFileError("resource_export_tenant_unavailable")
        return SessionFileExportSource(
            resource_id=updated.resource_id,
            session_key_hash=updated.session_key_hash,
            filename=updated.filename,
            size_bytes=updated.size_bytes,
            content_hash=updated.content_hash,
            canonical_path=source.canonical_path,
            tenant=tenant,
            transfer_id=updated.transfer_id,
            requires_upload=False,
        )

    def _entry_for_session(
        self,
        session_key: str,
        resource_id: str,
    ) -> tuple[Path, ManifestEntry]:
        root = self._workspace_root()
        try:
            entry = ManifestStore(root).get(resource_id)
        except RuntimeError as exc:
            raise SessionFileError("manifest_unavailable") from exc
        if entry is None or entry.status != "ready":
            raise SessionFileError("resource_not_found")
        if entry.session_key_hash != hash_identifier(session_key):
            raise SessionFileError("resource_not_found")
        return root, entry

    def _entry_for_session_hash(
        self,
        resource_id: str,
        session_key_hash: str,
    ) -> tuple[Path, ManifestEntry]:
        root = self._workspace_root()
        try:
            entry = ManifestStore(root).get(resource_id)
        except RuntimeError as exc:
            raise SessionFileError("manifest_unavailable") from exc
        if (
            entry is None
            or entry.status != "ready"
            or entry.session_key_hash != session_key_hash
        ):
            raise SessionFileError("resource_changed")
        return root, entry

    def _inspect(
        self,
        root: Path,
        entry: ManifestEntry,
        *,
        force_hash: bool,
    ) -> tuple[str, Path | None]:
        try:
            target = self._target(root, entry)
            canonical = target.resolve(strict=True)
            if not self._is_contained(root, canonical) or not canonical.is_file():
                return "missing", None
            stat = canonical.stat()
        except OSError:
            return "missing", None
        if stat.st_size != entry.size_bytes:
            return "changed", None
        observed_matches = (
            entry.observed_size == stat.st_size
            and entry.observed_mtime_ns == stat.st_mtime_ns
            and entry.observed_inode == getattr(stat, "st_ino", None)
        )
        if (force_hash or not observed_matches) and self._sha256(
            canonical
        ) != entry.content_hash:
            return "changed", None
        return "ready", canonical

    def _current_file(
        self,
        root: Path,
        entry: ManifestEntry,
    ) -> tuple[str, Path | None, int, str]:
        if entry.source_kind == "workspace_live":
            try:
                observed, digest = observe_workspace_file(root, Path(entry.relative_path))
                return "ready", root / entry.relative_path, observed.st_size, digest
            except (OSError, SessionFileError) as exc:
                state = "changed" if str(exc) == "resource_changed" else "missing"
                log.info("engine.session_files.live.unavailable resource_id=%s status=%s", entry.resource_id, state)
                return state, None, 0, ""
        try:
            target = self._target(root, entry)
            canonical = target.resolve(strict=True)
            if not self._is_contained(root, canonical) or not canonical.is_file():
                return "missing", None, 0, ""
            stat = canonical.stat()
            return "ready", canonical, stat.st_size, self._sha256(canonical)
        except OSError:
            return "missing", None, 0, ""

    def _remove_local_file(self, root: Path, entry: ManifestEntry) -> None:
        try:
            target = self._target(root, entry)
            resolved_parent = target.parent.resolve(strict=False)
            if not self._is_contained(root, resolved_parent):
                log.warning(
                    "engine.session_files.delete.skip resource_id=%s reason=parent_escape",
                    entry.resource_id,
                )
                return
            if target.is_symlink() or target.is_file():
                target.unlink(missing_ok=True)
            self._remove_empty_parents(root, target.parent)
        except OSError as exc:
            log.warning(
                "engine.session_files.delete.cleanup_failed resource_id=%s error_type=%s",
                entry.resource_id,
                type(exc).__name__,
            )

    @staticmethod
    def _remove_empty_parents(root: Path, directory: Path) -> None:
        stop = root / ".teamclaw/session-files"
        current = directory
        while current != stop and SessionFileService._is_contained(root, current):
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent

    @staticmethod
    def _target(root: Path, entry: ManifestEntry) -> Path:
        relative = Path(entry.relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise OSError("manifest path escapes workspace")
        target = root / relative
        if not SessionFileService._is_contained(root, target.resolve(strict=False)):
            raise OSError("manifest path escapes workspace")
        return target

    def _workspace_root(self) -> Path:
        value = self._workspace_root_provider()
        if value is None:
            raise SessionFileError("workspace_root_not_configured")
        root = Path(value)
        if not root.is_absolute():
            raise SessionFileError("workspace_root_not_configured")
        try:
            return root.resolve(strict=True)
        except OSError as exc:
            raise SessionFileError("workspace_root_not_configured") from exc

    @staticmethod
    def _is_contained(root: Path, target: Path) -> bool:
        return target == root or root in target.parents

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
