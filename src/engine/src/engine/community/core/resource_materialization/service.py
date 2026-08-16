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
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar
from urllib.parse import quote

from engine.community.core.resource_materialization.models import (
    ChatAttachmentMaterializationRequest,
    ManifestEntry,
    MaterializationRequest,
    MaterializationResult,
    MaterializedContent,
    PreparedChatAttachment,
)
from engine.community.plugin_api.resource_materialization import (
    BaasMaterializationClient,
    BackendMaterializationCallbackClient,
    TemporaryUrlPullClient,
)
from engine.community.plugin_api.workspace_root import workspace_root_strict

log = logging.getLogger("engine.resource_materialization")
_SAFE_IDENTIFIER_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
_WINDOWS_FORBIDDEN_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')
_MAX_FILENAME_UTF8_BYTES = 255
_DEFAULT_MAX_CHAT_IMAGE_BYTES = 20 * 1024 * 1024


def build_session_file_relative_path(
    *,
    scope_key_hash: str,
    session_key_hash: str,
    resource_id: str,
    filename: str,
) -> Path:
    """Build the version-1 controlled Session File workspace layout."""
    identifier_segments = (scope_key_hash, session_key_hash, resource_id)
    if any(
        not _SAFE_IDENTIFIER_SEGMENT.fullmatch(segment)
        for segment in identifier_segments
    ):
        raise MaterializationSecurityError("invalid controlled path segment")
    try:
        filename_bytes = filename.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise MaterializationSecurityError("invalid controlled path segment") from exc
    if (
        not filename
        or Path(filename).name != filename
        or filename in {".", ".."}
        or len(filename_bytes) > _MAX_FILENAME_UTF8_BYTES
        or any(
            not character.isprintable()
            or character in _WINDOWS_FORBIDDEN_FILENAME_CHARACTERS
            for character in filename
        )
    ):
        raise MaterializationSecurityError("invalid controlled path segment")
    return Path(
        ".teamclaw",
        "session-files",
        scope_key_hash,
        session_key_hash,
        resource_id,
        filename,
    )


class MaterializationSecurityError(ValueError):
    """The requested workspace path escaped or violated the controlled root."""


class ResourceNotMaterializedError(ValueError):
    """The requested resource has no readable ready workspace file."""


class ChatAttachmentPreparationError(RuntimeError):
    """Safe reason for rejecting a temporary chat attachment."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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

    def replace_export_source(
        self,
        *,
        resource_id: str,
        scope_key_hash: str,
        session_key_hash: str,
        transfer_id: str,
        size_bytes: int,
        content_hash: str,
        observed_size: int,
        observed_mtime_ns: int,
        observed_inode: int | None,
        baas_tenant: str,
    ) -> ManifestEntry | None:
        """Atomically replace the BaaS source after a verified Engine re-upload."""
        with self._lock_for(self.path):
            payload = self._read_locked()
            value = payload["resources"].get(resource_id)
            if value is None:
                return None
            entry = ManifestEntry.model_validate(value)
            if (
                entry.status != "ready"
                or entry.scope_key_hash != scope_key_hash
                or entry.session_key_hash != session_key_hash
            ):
                return None
            updated = entry.model_copy(
                update={
                    "transfer_id": transfer_id,
                    "size_bytes": size_bytes,
                    "content_hash": content_hash,
                    "observed_size": observed_size,
                    "observed_mtime_ns": observed_mtime_ns,
                    "observed_inode": observed_inode,
                    "baas_tenant": baas_tenant,
                    "source_kind": "baas_session_file",
                    "source_attachment_id": None,
                    "source_url_hash": None,
                }
            )
            payload["resources"][resource_id] = updated.model_dump(mode="json")
            payload["version"] = 1
            self._write_locked(payload)
            return updated

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
        temporary_url_pull_client: TemporaryUrlPullClient | None = None,
        workspace_root_provider: Callable[[], Path | None] = workspace_root_strict,
        max_chat_image_bytes: int = _DEFAULT_MAX_CHAT_IMAGE_BYTES,
    ) -> None:
        if max_chat_image_bytes <= 0:
            raise ValueError("chat image size limit must be positive")
        self._pull_client = pull_client
        self._callback_client = callback_client
        self._temporary_url_pull_client = temporary_url_pull_client
        self._workspace_root_provider = workspace_root_provider
        self._max_chat_image_bytes = max_chat_image_bytes
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
            if (
                not canonical.is_file()
                or canonical.stat().st_size != entry.size_bytes
                or self._sha256(canonical) != entry.content_hash
            ):
                raise ResourceNotMaterializedError("resource_not_materialized")
            if self._sha256(canonical) != entry.content_hash:
                raise ResourceNotMaterializedError("resource_not_materialized")
        except (OSError, MaterializationSecurityError) as exc:
            raise ResourceNotMaterializedError("resource_not_materialized") from exc
        media_type = (
            mimetypes.guess_type(entry.filename)[0] or "application/octet-stream"
        )
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
                    "engine.resource_materialize.path.reject resource_id=%s task_version=%s error_type=%s reason=%s",
                    request.resource_id,
                    request.task_version,
                    type(exc).__name__,
                    str(exc),
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

    async def materialize_chat_attachment(
        self,
        request: ChatAttachmentMaterializationRequest,
    ) -> MaterializationResult:
        """Materialize a chat capability without Backend callback or HTTP recursion."""
        session_key_hash = hashlib.sha256(
            request.session_key.encode("utf-8")
        ).hexdigest()
        resource_id = self._chat_resource_id(
            request.scope_key_hash,
            session_key_hash,
            request.attachment_id,
        )
        relative = build_session_file_relative_path(
            scope_key_hash=request.scope_key_hash,
            session_key_hash=session_key_hash,
            resource_id=resource_id,
            filename=request.filename,
        )
        internal = MaterializationRequest(
            resource_id=resource_id,
            transfer_id=f"tmp_{self._url_hash(request.temporary_url)[:32]}",
            task_id=f"chat_{resource_id}",
            task_version=1,
            scope_key_hash=request.scope_key_hash,
            session_key_hash=session_key_hash,
            transfer_api_version="bot_device_v1",
            device_path=relative.as_posix(),
            filename=request.filename,
            size_bytes=request.size_bytes,
            content_hash=request.content_hash.lower() if request.content_hash else None,
        )
        async with self._lock:
            if request.expires_at_ms is not None and request.expires_at_ms <= int(
                time.time() * 1000
            ):
                return self._failure_result(internal, "temporary_url_expired")
            if self._temporary_url_pull_client is None:
                return self._failure_result(
                    internal, "temporary_url_pull_not_configured"
                )
            try:
                return await self._materialize_locked(internal, chat_request=request)
            except MaterializationSecurityError:
                return self._failure_result(internal, "invalid_device_path")
            except Exception as exc:
                log.warning(
                    "engine.resource_materialize.chat.fail resource_id=%s error_type=%s",
                    resource_id,
                    type(exc).__name__,
                )
                return self._failure_result(internal, "pull_failed")

    async def prepare_chat_image_attachment(
        self,
        request: ChatAttachmentMaterializationRequest,
    ) -> PreparedChatAttachment:
        """Download and validate a temporary image without publishing it."""
        if request.expires_at_ms is not None and request.expires_at_ms <= int(
            time.time() * 1000
        ):
            raise ChatAttachmentPreparationError("temporary_url_expired")
        if self._temporary_url_pull_client is None:
            raise ChatAttachmentPreparationError(
                "temporary_url_pull_not_configured"
            )

        # COSEC: validate the provider filename before OpenClaw may stage it.
        try:
            build_session_file_relative_path(
                scope_key_hash=request.scope_key_hash,
                session_key_hash=hashlib.sha256(
                    request.session_key.encode("utf-8")
                ).hexdigest(),
                resource_id="chat-image",
                filename=request.filename,
            )
        except MaterializationSecurityError as exc:
            raise ChatAttachmentPreparationError("invalid_filename") from exc
        if (
            request.size_bytes is not None
            and request.size_bytes > self._max_chat_image_bytes
        ):
            raise ChatAttachmentPreparationError("size_limit_exceeded")

        descriptor, temporary_name = tempfile.mkstemp(prefix=".engine-chat-image-")
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            pull_request = request.model_copy(
                update={"download_max_bytes": self._max_chat_image_bytes}
            )
            await self._temporary_url_pull_client.pull(pull_request, temporary)
            if not temporary.is_file():
                raise ChatAttachmentPreparationError("pull_missing_file")
            actual_size = temporary.stat().st_size
            if actual_size > self._max_chat_image_bytes:
                raise ChatAttachmentPreparationError("size_limit_exceeded")
            if request.size_bytes is not None and actual_size != request.size_bytes:
                raise ChatAttachmentPreparationError("size_mismatch")
            actual_hash = await asyncio.to_thread(self._sha256, temporary)
            if (
                request.content_hash is not None
                and actual_hash != request.content_hash.lower()
            ):
                raise ChatAttachmentPreparationError("hash_mismatch")
            content = await asyncio.to_thread(temporary.read_bytes)
            detected_media_type = self._detect_image_media_type(content)
            if detected_media_type is None:
                raise ChatAttachmentPreparationError("invalid_image_content")
            declared_media_type = (
                request.media_type.lower().split(";", 1)[0].strip()
                if request.media_type
                else None
            )
            aliases = {
                "image/jpg": "image/jpeg",
                "image/x-png": "image/png",
            }
            declared_media_type = aliases.get(
                declared_media_type, declared_media_type
            )
            if (
                declared_media_type is not None
                and declared_media_type != detected_media_type
            ):
                raise ChatAttachmentPreparationError("media_type_mismatch")
            return PreparedChatAttachment(
                attachment_id=request.attachment_id,
                filename=request.filename,
                media_type=detected_media_type,
                content=content,
            )
        except asyncio.CancelledError:
            raise
        except ChatAttachmentPreparationError:
            raise
        except Exception as exc:
            attachment_hash = hashlib.sha256(
                request.attachment_id.encode("utf-8")
            ).hexdigest()[:16]
            log.warning(
                "engine.resource_materialize.image.fail attachment_hash=%s error_type=%s",
                attachment_hash,
                type(exc).__name__,
            )
            raise ChatAttachmentPreparationError("pull_failed") from exc
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _detect_image_media_type(content: bytes) -> str | None:
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        ):
            return "image/webp"
        return None

    async def remove_chat_materialization(self, resource_id: str) -> None:
        """Rollback one just-created chat materialization after batch failure."""
        async with self._lock:
            root = self._workspace_root()
            entry = ManifestStore(root).remove(resource_id)
            if entry is None or entry.source_kind != "temporary_url":
                return
            target = root / Path(entry.relative_path)
            self._assert_contained(root, target)
            target.unlink(missing_ok=True)

    async def _materialize_locked(
        self,
        request: MaterializationRequest,
        *,
        chat_request: ChatAttachmentMaterializationRequest | None = None,
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
        # Keep the temporary segment independent of the user filename: the final
        # filename may already be close to the filesystem's per-segment limit.
        temporary = target.with_name(f".part-{uuid.uuid4().hex}")
        log.info(
            "engine.resource_materialize.pull.start resource_id=%s task_version=%s path_hash=%s",
            request.resource_id,
            request.task_version,
            self._path_hash(target),
        )
        try:
            if chat_request is None:
                await self._pull_client.pull(request, temporary)
            else:
                assert self._temporary_url_pull_client is not None
                await self._temporary_url_pull_client.pull(chat_request, temporary)
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
                uploaded_at=request.uploaded_at,
                baas_tenant=request.tenant,
                source_kind="temporary_url"
                if chat_request is not None
                else "baas_session_file",
                source_attachment_id=(
                    chat_request.attachment_id if chat_request is not None else None
                ),
                source_url_hash=(
                    self._url_hash(chat_request.temporary_url)
                    if chat_request is not None
                    else None
                ),
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
        relative = build_session_file_relative_path(
            scope_key_hash=request.scope_key_hash,
            session_key_hash=request.session_key_hash,
            resource_id=request.resource_id,
            filename=request.filename,
        )
        supplied_path = request.workspace_relative_path or request.device_path
        if supplied_path is None:
            raise MaterializationSecurityError("controlled path is missing")
        supplied = PurePosixPath(supplied_path.replace("\\", "/"))
        if ".." in supplied.parts:
            raise MaterializationSecurityError("device_path traversal is forbidden")
        expected_suffix = relative.parts
        if request.transfer_api_version == "session_v2":
            if tuple(supplied.parts) != expected_suffix:
                raise MaterializationSecurityError(
                    "workspace path does not match resource scope"
                )
        elif tuple(supplied.parts[-len(expected_suffix) :]) != expected_suffix:
            raise MaterializationSecurityError(
                "device_path does not match resource scope"
            )
        target = root / relative
        self._assert_contained(root, target)
        return relative, target

    @staticmethod
    def _assert_contained(root: Path, target: Path) -> None:
        # COSEC: Path.resolve follows symlinks before the parent relation check;
        # string-prefix checks are insufficient for sibling-prefix and symlink escapes.
        resolved_root = root.resolve(strict=True)
        resolved_target = target.resolve(strict=False)
        if (
            resolved_target != resolved_root
            and resolved_root not in resolved_target.parents
        ):
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

    @staticmethod
    def _url_hash(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    @staticmethod
    def _chat_resource_id(
        scope_key_hash: str,
        session_key_hash: str,
        attachment_id: str,
    ) -> str:
        digest = hashlib.sha256(
            f"{scope_key_hash}\0{session_key_hash}\0{attachment_id}".encode("utf-8")
        ).hexdigest()
        return f"sr_{digest[:40]}"
