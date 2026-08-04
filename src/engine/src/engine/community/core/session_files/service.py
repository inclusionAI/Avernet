"""Serve ready session files directly from the controlled Engine workspace."""
from __future__ import annotations

import hashlib
import logging
import mimetypes
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
from engine.community.core.session_files.models import SessionFileError, SessionFileView
from engine.community.plugin_api.workspace_root import workspace_root_strict

log = logging.getLogger("engine.session_files")


class SessionFileService:
    """Manifest-only list, content, and deletion operations for one session."""

    def __init__(
        self,
        *,
        workspace_root_provider: Callable[[], Path | None] = workspace_root_strict,
    ) -> None:
        self._workspace_root_provider = workspace_root_provider

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
                uploaded_at=self._format_uploaded_at(entry.uploaded_at),
            )
            for entry in entries
            if entry.status == "ready" and entry.session_key_hash == session_hash
        ]
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
        availability, canonical = self._inspect(root, entry, force_hash=True)
        if availability != "ready" or canonical is None:
            raise SessionFileError(f"resource_{availability}")
        media_type = mimetypes.guess_type(entry.filename)[0] or "application/octet-stream"
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
        if (force_hash or not observed_matches) and self._sha256(canonical) != entry.content_hash:
            return "changed", None
        return "ready", canonical

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
