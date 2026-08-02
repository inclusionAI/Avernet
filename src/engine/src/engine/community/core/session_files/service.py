"""Serve ready session files directly from the controlled Engine workspace."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from engine.community.core.resource_materialization.models import (
    ManifestEntry,
    MaterializedContent,
    hash_identifier,
)
from engine.community.core.resource_materialization.service import ManifestStore
from engine.community.core.session_files.models import (
    SessionFileError,
    SessionFileExportSource,
    SessionFileView,
)
from engine.community.plugin_api.workspace_root import workspace_root_strict

log = logging.getLogger("engine.session_files")


class SessionFileService:
    """Manifest-only list, content, and deletion operations for one session."""

    def __init__(
        self,
        *,
        workspace_root_provider: Callable[[], Path | None] = workspace_root_strict,
        default_baas_tenant: str | None = None,
    ) -> None:
        self._workspace_root_provider = workspace_root_provider
        self._default_baas_tenant = default_baas_tenant

    def list_files(self, *, session_key: str) -> list[SessionFileView]:
        root = self._workspace_root()
        session_hash = hash_identifier(session_key)
        try:
            entries = ManifestStore(root).list_entries()
        except RuntimeError as exc:
            raise SessionFileError("manifest_unavailable") from exc
        files = [
            SessionFileView(
                resource_id=entry.resource_id,
                display_name=entry.filename,
                size_bytes=entry.size_bytes,
                availability=self._inspect(root, entry, force_hash=False)[0],
            )
            for entry in entries
            if entry.status == "ready" and entry.session_key_hash == session_hash
        ]
        log.info(
            "engine.session_files.list session_key_hash=%s file_count=%s",
            session_hash[:16],
            len(files),
        )
        return files

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
            entry.size_bytes,
        )
        return MaterializedContent(
            path=canonical,
            filename=entry.filename,
            media_type=media_type,
            content_disposition=content_disposition,
            size_bytes=entry.size_bytes,
        )

    def delete_file(self, *, session_key: str, resource_id: str) -> None:
        root, entry = self._entry_for_session(session_key, resource_id)
        removed = ManifestStore(root).remove(resource_id)
        if removed is None:
            raise SessionFileError("resource_not_found")
        self._remove_local_file(root, entry)
        log.info(
            "engine.session_files.delete resource_id=%s session_key_hash=%s",
            resource_id,
            hash_identifier(session_key)[:16],
        )

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
            size_bytes != entry.size_bytes or content_hash != entry.content_hash
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
            raise SessionFileError(f"resource_{availability}")

    def create_export_snapshot(self, source: SessionFileExportSource) -> Path:
        """Copy a verified current file before an Engine-originated upload."""
        self.revalidate_export_source(source)
        root = self._workspace_root()
        directory = root / ".teamclaw/session-files/.exports"
        directory.mkdir(parents=True, exist_ok=True)
        snapshot = directory / f".export-{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        total = 0
        try:
            with (
                source.canonical_path.open("rb") as source_stream,
                snapshot.open("xb") as target,
            ):
                for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                    total += len(chunk)
                    digest.update(chunk)
                    target.write(chunk)
            if total != source.size_bytes or digest.hexdigest() != source.content_hash:
                raise SessionFileError("resource_changed")
            self.revalidate_export_source(source)
            return snapshot
        except (OSError, SessionFileError):
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
