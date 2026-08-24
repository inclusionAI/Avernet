"""Application service for explicit Chat workspace file sharing."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import replace
import hashlib
import logging
import stat
from pathlib import Path

from engine.community.core.chat_file_share.models import (
    ChatFileShareError,
    ChatFileShareResult,
)
from engine.community.core.session_files.models import SessionFileTransferRequest
from engine.community.plugin_api.session_file_export import (
    BaasFileExportError,
    BaasSessionFileClient,
)

log = logging.getLogger("engine.chat_file_share")
_SUPPRESS_HTTPX_ACCESS_LOG = ContextVar(
    "chat_file_share_suppress_httpx_access_log",
    default=False,
)


class _ChatFileShareHttpxFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _SUPPRESS_HTTPX_ACCESS_LOG.get()


logging.getLogger("httpx").addFilter(_ChatFileShareHttpxFilter())

_SHARE_LINK_TTL_SECONDS = 86400
class ChatFileShareService:
    """Share one validated workspace file through the Session File protocol."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        tenant: str,
        client: BaasSessionFileClient,
    ) -> None:
        if not tenant:
            raise ValueError("tenant is required")
        self._workspace_root = workspace_root
        self._tenant = tenant
        self._client = client

    async def share(
        self,
        *,
        relative_path: str,
        session_key: str,
    ) -> ChatFileShareResult:
        if not session_key.strip():
            raise ChatFileShareError("session_context_unavailable")
        source = self._resolve_workspace_file(relative_path)
        path_hash = self._path_hash(relative_path)
        log.info(
            "engine.chat_file_share.start path_hash=%s size_bytes=%s",
            path_hash,
            source.stat().st_size,
        )
        request = SessionFileTransferRequest(
            resource_id=self._resource_id(session_key, relative_path),
            tenant=self._tenant,
            session_key=session_key,
            transfer_id="",
        )
        log_token = _SUPPRESS_HTTPX_ACCESS_LOG.set(True)
        try:
            grant = await self._client.create_upload_grant(
                request,
                filename=source.name,
                size_bytes=source.stat().st_size,
            )
            await self._client.upload_file(
                grant,
                str(source),
                resource_id=request.resource_id,
            )
            completed_request = replace(request, transfer_id=grant.transfer_id)
            await self._client.complete_upload(completed_request)
            link = await self._client.create_share_link(
                completed_request,
                expire_seconds=_SHARE_LINK_TTL_SECONDS,
            )
        except BaasFileExportError as exc:
            raise ChatFileShareError(self._share_error_code(exc.code)) from exc
        finally:
            _SUPPRESS_HTTPX_ACCESS_LOG.reset(log_token)
        result = ChatFileShareResult(
            file_name=source.name,
            size_bytes=source.stat().st_size,
            share_url=link.download_url,
            expires_at=link.expires_at,
        )
        log.info(
            "engine.chat_file_share.ready path_hash=%s size_bytes=%s",
            path_hash,
            result.size_bytes,
        )
        return result

    def _resolve_workspace_file(self, relative_path: str) -> Path:
        relative = Path(relative_path)
        if (
            not relative_path
            or relative.is_absolute()
            or ".." in relative.parts
            or not relative.parts
        ):
            raise ChatFileShareError("invalid_file_path")
        try:
            root = self._workspace_root.resolve(strict=True)
            if not root.is_dir():
                raise OSError("workspace is not a directory")
            candidate = root / relative
            current = root
            for part in relative.parts:
                current = current / part
                # COSEC: reject symlink hops before canonicalisation so a Chat
                # request cannot use the workspace as a path traversal bridge.
                if current.is_symlink():
                    raise OSError("workspace path contains symlink")
            source = candidate.resolve(strict=True)
            if root not in source.parents or not stat.S_ISREG(source.stat().st_mode):
                raise OSError("workspace path is not a regular contained file")
            return source
        except OSError as exc:
            raise ChatFileShareError("invalid_file_path") from exc

    @staticmethod
    def _path_hash(relative_path: str) -> str:
        return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _resource_id(session_key: str, relative_path: str) -> str:
        value = "{}\0{}".format(session_key, relative_path)
        return "chat-share-{}".format(
            hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        )

    @staticmethod
    def _share_error_code(code: str) -> str:
        return {
            "file_export_unavailable": "file_share_unavailable",
            "file_export_timeout": "file_share_timeout",
        }.get(code, "file_share_failed")
