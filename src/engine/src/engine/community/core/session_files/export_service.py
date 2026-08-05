"""In-memory coordinator for large Session File attachment downloads."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from engine.community.core.session_files.models import (
    SessionFileError,
    SessionFileExportSource,
    SessionFileExternalDownload,
    SessionFileTransferRequest,
)
from engine.community.core.session_files.service import SessionFileService
from engine.community.plugin_api.session_file_export import (
    BaasFileExportError,
    BaasSessionFileClient,
)

log = logging.getLogger("engine.session_file_export")
EXPORT_POLL_SECONDS = 2
EXPORT_SHARE_LINK_TTL_SECONDS = 7200
EXPORT_ERROR_CACHE_SECONDS = 2


@dataclass
class _ExportJob:
    task: asyncio.Task[None]
    download: SessionFileExternalDownload | None = None
    expires_monotonic: float = 0.0
    error_code: str | None = None
    error_expires_monotonic: float = 0.0


@dataclass(frozen=True)
class ExportRequestResult:
    state: str
    download: SessionFileExternalDownload | None = None
    error_code: str | None = None


class SessionFileExportService:
    """Keep large file bytes out of proxypass while preserving Engine authority."""

    def __init__(
        self,
        *,
        session_file_service: SessionFileService,
        export_client: BaasSessionFileClient,
        share_link_ttl_seconds: int = EXPORT_SHARE_LINK_TTL_SECONDS,
    ) -> None:
        self._session_file_service = session_file_service
        self._export_client = export_client
        self._share_link_ttl_seconds = share_link_ttl_seconds
        self._lock = asyncio.Lock()
        self._jobs: dict[tuple[str, str, str], _ExportJob] = {}

    async def request_download(
        self,
        *,
        source: SessionFileExportSource,
        session_key: str,
    ) -> ExportRequestResult:
        key = (source.resource_id, source.content_hash, source.transfer_id)
        now = time.monotonic()
        async with self._lock:
            job = self._jobs.get(key)
            if job is not None:
                if job.download is not None and now < job.expires_monotonic:
                    return ExportRequestResult(state="ready", download=job.download)
                if job.error_code is not None and now < job.error_expires_monotonic:
                    return ExportRequestResult(
                        state="failed", error_code=job.error_code
                    )
                if not job.task.done():
                    return ExportRequestResult(state="preparing")
                self._jobs.pop(key, None)
            task = asyncio.create_task(self._export(key, source, session_key))
            self._jobs[key] = _ExportJob(task=task)
        log.info(
            "engine.session_file_export.queued resource_id=%s requires_upload=%s",
            source.resource_id,
            source.requires_upload,
        )
        return ExportRequestResult(state="preparing")

    async def _export(
        self,
        key: tuple[str, str, str],
        source: SessionFileExportSource,
        session_key: str,
    ) -> None:
        error_code: str | None = None
        download: SessionFileExternalDownload | None = None
        try:
            active_source = source
            if active_source.requires_upload:
                active_source = await self._reupload(active_source, session_key)
            try:
                share_link = await self._create_share_link(active_source, session_key)
            except BaasFileExportError as exc:
                if exc.code != "file_export_source_missing":
                    raise
                active_source = await self._reupload(active_source, session_key)
                share_link = await self._create_share_link(active_source, session_key)
            await asyncio.to_thread(
                self._session_file_service.revalidate_export_source, active_source
            )
            download = SessionFileExternalDownload(
                download_url=share_link.download_url,
                expires_at=share_link.expires_at,
                filename=active_source.filename,
                size_bytes=active_source.size_bytes,
            )
            cache_ttl_seconds = self._cache_ttl_seconds(share_link.expires_at)
            log.info(
                "engine.session_file_export.ready resource_id=%s size_bytes=%s",
                active_source.resource_id,
                active_source.size_bytes,
            )
        except SessionFileError as exc:
            error_code = str(exc)
            log.info(
                "engine.session_file_export.changed resource_id=%s error_code=%s",
                source.resource_id,
                error_code,
            )
        except BaasFileExportError as exc:
            error_code = exc.code
            log.warning(
                "engine.session_file_export.failed resource_id=%s error_code=%s",
                source.resource_id,
                error_code,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            error_code = "file_export_failed"
            log.warning(
                "engine.session_file_export.failed resource_id=%s error_type=%s",
                source.resource_id,
                type(exc).__name__,
            )
        async with self._lock:
            job = self._jobs.get(key)
            if job is None:
                return
            if download is not None:
                job.download = download
                job.expires_monotonic = time.monotonic() + cache_ttl_seconds
                return
            if error_code == "resource_changed":
                self._jobs.pop(key, None)
                return
            job.error_code = error_code or "file_export_failed"
            job.error_expires_monotonic = time.monotonic() + EXPORT_ERROR_CACHE_SECONDS

    async def _reupload(
        self,
        source: SessionFileExportSource,
        session_key: str,
    ) -> SessionFileExportSource:
        snapshot = await asyncio.to_thread(
            self._session_file_service.create_export_snapshot, source
        )
        try:
            request = self._request(source, session_key)
            grant = await self._export_client.create_upload_grant(
                request,
                filename=source.filename,
                size_bytes=source.size_bytes,
            )
            await self._export_client.upload_file(
                grant,
                str(snapshot),
                resource_id=source.resource_id,
            )
            await self._export_client.complete_upload(
                self._request(source, session_key, transfer_id=grant.transfer_id)
            )
            promoted = await asyncio.to_thread(
                self._session_file_service.promote_export_source,
                source,
                transfer_id=grant.transfer_id,
            )
            log.info(
                "engine.session_file_export.reupload.done resource_id=%s size_bytes=%s",
                source.resource_id,
                source.size_bytes,
            )
            return promoted
        finally:
            await asyncio.to_thread(Path(snapshot).unlink, missing_ok=True)

    async def _create_share_link(
        self,
        source: SessionFileExportSource,
        session_key: str,
    ):
        return await self._export_client.create_share_link(
            self._request(source, session_key),
            expire_seconds=self._share_link_ttl_seconds,
        )

    @staticmethod
    def _request(
        source: SessionFileExportSource,
        session_key: str,
        *,
        transfer_id: str | None = None,
    ) -> SessionFileTransferRequest:
        return SessionFileTransferRequest(
            resource_id=source.resource_id,
            tenant=source.tenant,
            session_key=session_key,
            transfer_id=transfer_id or source.transfer_id,
        )

    def _cache_ttl_seconds(self, expires_at: str) -> float:
        try:
            expiry = datetime.fromisoformat(expires_at)
        except ValueError as exc:
            raise BaasFileExportError("file_export_failed") from exc
        if expiry.tzinfo is None:
            raise BaasFileExportError("file_export_failed")
        seconds = (expiry.astimezone(UTC) - datetime.now(UTC)).total_seconds()
        if seconds <= 0:
            raise BaasFileExportError("file_export_failed")
        return min(seconds, float(self._share_link_ttl_seconds))
