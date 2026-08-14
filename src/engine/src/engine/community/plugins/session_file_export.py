"""HTTP Session File client for large attachment share links and re-uploads."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from urllib.parse import quote, urlsplit

import httpx

from engine.community.core.session_files.models import (
    BaasFileExportShareLink,
    SessionFileTransferRequest,
    SessionFileUploadGrant,
)
from engine.community.plugin_api.session_file_export import BaasFileExportError

log = logging.getLogger("engine.session_file_export")


class NotConfiguredBaasSessionFileClient:
    async def create_share_link(
        self,
        request: SessionFileTransferRequest,
        *,
        expire_seconds: int,
    ) -> BaasFileExportShareLink:
        raise BaasFileExportError("file_export_unavailable")

    async def create_upload_grant(
        self,
        request: SessionFileTransferRequest,
        *,
        filename: str,
        size_bytes: int,
    ) -> SessionFileUploadGrant:
        raise BaasFileExportError("file_export_unavailable")

    async def upload_file(
        self,
        grant: SessionFileUploadGrant,
        source_path: str,
        *,
        resource_id: str,
    ) -> None:
        raise BaasFileExportError("file_export_unavailable")

    async def complete_upload(self, request: SessionFileTransferRequest) -> None:
        raise BaasFileExportError("file_export_unavailable")


class BaasSessionFileClient:
    """Use Session File APIs without a Bot UUID or caller-supplied path."""

    def __init__(
        self,
        *,
        baas_base_url: str,
        control_headers: Mapping[str, str],
        allowed_share_hosts: frozenset[str],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(baas_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("invalid BaaS base URL")
        if not allowed_share_hosts:
            raise ValueError("allowed_share_hosts is required")
        self._baas_base_url = baas_base_url.rstrip("/")
        self._control_headers = dict(control_headers)
        self._allowed_share_hosts = {host.lower() for host in allowed_share_hosts}
        self._transport = transport

    async def create_share_link(
        self,
        request: SessionFileTransferRequest,
        *,
        expire_seconds: int,
    ) -> BaasFileExportShareLink:
        data = await self._request(
            "POST",
            self._path(
                request,
                "files/transfers/{}/share-link".format(
                    quote(request.transfer_id, safe="")
                ),
            ),
            json={
                "expire_seconds": expire_seconds,
                "show": False,
                "operator": "engine",
            },
            resource_id=request.resource_id,
        )
        url = data.get("share_url") or data.get("download_url")
        expires_at = data.get("expires_at")
        if not isinstance(url, str) or not isinstance(expires_at, str):
            raise BaasFileExportError("file_export_failed")
        self._validate_share_url(url)
        return BaasFileExportShareLink(download_url=url, expires_at=expires_at)

    async def create_upload_grant(
        self,
        request: SessionFileTransferRequest,
        *,
        filename: str,
        size_bytes: int,
    ) -> SessionFileUploadGrant:
        data = await self._request(
            "POST",
            self._path(request, "files/upload-url"),
            json={
                "filename": filename,
                "file_size": size_bytes,
                "operator": "engine",
                "expire_seconds": 3600,
            },
            resource_id=request.resource_id,
        )
        return SessionFileUploadGrant(
            transfer_id=self._required_string(data, "transfer_id"),
            upload_type=self._required_string(data, "type").upper(),
            upload_url=self._optional_string(data, "upload_url"),
            http_method=self._optional_string(data, "http_method") or "PUT",
            part_size=self._optional_int(data, "part_size"),
            part_count=self._optional_int(data, "part_count"),
            parts=self._optional_parts(data),
        )

    async def upload_file(
        self,
        grant: SessionFileUploadGrant,
        source_path: str,
        *,
        resource_id: str,
    ) -> None:
        from pathlib import Path

        source = Path(source_path)
        try:
            size_bytes = source.stat().st_size
        except OSError as exc:
            raise BaasFileExportError("file_export_failed") from exc
        if grant.upload_type == "SINGLE":
            if not grant.upload_url:
                raise BaasFileExportError("file_export_failed")
            await self._put_file(
                grant.upload_url,
                grant.http_method,
                source,
                0,
                size_bytes,
                resource_id=resource_id,
            )
            return
        if (
            grant.upload_type != "MULTIPART"
            or grant.part_size is None
            or grant.part_count is None
            or grant.parts is None
            or grant.part_count != len(grant.parts)
        ):
            raise BaasFileExportError("file_export_failed")
        offset = 0
        for index, part in enumerate(grant.parts):
            length = min(grant.part_size, size_bytes - offset)
            if length <= 0:
                raise BaasFileExportError("file_export_failed")
            await self._put_file(
                self._part_url(part),
                self._part_method(part, grant.http_method),
                source,
                offset,
                length,
                resource_id=resource_id,
                headers=self._part_headers(part),
            )
            offset += length
            if (
                not isinstance(part.get("part_number"), int)
                or part["part_number"] != index + 1
            ):
                raise BaasFileExportError("file_export_failed")
        if offset != size_bytes:
            raise BaasFileExportError("file_export_failed")

    async def complete_upload(self, request: SessionFileTransferRequest) -> None:
        data = await self._request(
            "POST",
            self._path(
                request,
                "files/upload-url/{}/complete".format(
                    quote(request.transfer_id, safe="")
                ),
            ),
            resource_id=request.resource_id,
        )
        if self._required_string(data, "status").upper() != "DONE":
            raise BaasFileExportError("file_export_failed")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        resource_id: str,
        json: dict | None = None,
    ) -> dict:
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0)
        try:
            async with httpx.AsyncClient(
                base_url=self._baas_base_url,
                headers=self._control_headers,
                timeout=timeout,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            log.warning(
                "engine.session_file_export.transport_fail resource_id=%s error_type=%s",
                resource_id,
                type(exc).__name__,
            )
            raise BaasFileExportError("file_export_failed") from exc
        if response.status_code in {404, 410}:
            raise BaasFileExportError("file_export_source_missing")
        if response.status_code == 503:
            raise BaasFileExportError("file_export_unavailable")
        if response.status_code == 504:
            raise BaasFileExportError("file_export_timeout")
        if response.is_error:
            log.warning(
                "engine.session_file_export.http_fail resource_id=%s status_code=%s",
                resource_id,
                response.status_code,
            )
            raise BaasFileExportError("file_export_failed")
        try:
            payload = response.json()
        except ValueError as exc:
            raise BaasFileExportError("file_export_failed") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("code") not in {None, 0}
            or not isinstance(payload.get("data"), dict)
        ):
            raise BaasFileExportError("file_export_failed")
        return payload["data"]

    @staticmethod
    def _path(request: SessionFileTransferRequest, operation: str) -> str:
        return "/api/v1/sessions/{}/{}/{}".format(
            quote(request.tenant, safe=""),
            quote(request.session_key, safe=""),
            operation,
        )

    @staticmethod
    def _required_string(data: dict, key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value:
            raise BaasFileExportError("file_export_failed")
        return value

    @staticmethod
    def _optional_string(data: dict, key: str) -> str | None:
        value = data.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise BaasFileExportError("file_export_failed")
        return value

    @staticmethod
    def _optional_int(data: dict, key: str) -> int | None:
        value = data.get(key)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise BaasFileExportError("file_export_failed")
        return value

    @staticmethod
    def _optional_parts(data: dict) -> list[dict] | None:
        value = data.get("parts")
        if value is None:
            return None
        if not isinstance(value, list) or not all(
            isinstance(part, dict) for part in value
        ):
            raise BaasFileExportError("file_export_failed")
        return value

    def _validate_share_url(self, share_url: str) -> None:
        parsed = urlsplit(share_url)
        host = parsed.hostname.lower() if parsed.hostname else ""
        if (
            parsed.scheme != "https"
            or not host
            or host not in self._allowed_share_hosts
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise BaasFileExportError("file_export_failed")

    async def _put_file(
        self,
        upload_url: str,
        method: str,
        source_path,
        offset: int,
        length: int,
        *,
        resource_id: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._validate_share_url(upload_url)
        if method.upper() not in {"PUT", "POST"}:
            raise BaasFileExportError("file_export_failed")

        async def body():
            stream = await asyncio.to_thread(source_path.open, "rb")
            try:
                await asyncio.to_thread(stream.seek, offset)
                remaining = length
                while remaining:
                    chunk = await asyncio.to_thread(
                        stream.read, min(1024 * 1024, remaining)
                    )
                    if not chunk:
                        raise BaasFileExportError("file_export_failed")
                    remaining -= len(chunk)
                    yield chunk
            finally:
                await asyncio.to_thread(stream.close)

        request_headers = {"Content-Length": str(length)}
        if headers:
            request_headers.update(headers)
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=60.0, write=600.0, pool=30.0),
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.request(
                    method.upper(), upload_url, content=body(), headers=request_headers
                )
            response.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            log.warning(
                "engine.session_file_export.upload_fail resource_id=%s error_type=%s",
                resource_id,
                type(exc).__name__,
            )
            raise BaasFileExportError("file_export_failed") from exc

    @staticmethod
    def _part_url(part: dict) -> str:
        value = part.get("upload_url") or part.get("url")
        if not isinstance(value, str) or not value:
            raise BaasFileExportError("file_export_failed")
        return value

    @staticmethod
    def _part_method(part: dict, default: str) -> str:
        value = part.get("http_method") or part.get("method") or default
        if not isinstance(value, str) or not value:
            raise BaasFileExportError("file_export_failed")
        return value

    @staticmethod
    def _part_headers(part: dict) -> Mapping[str, str] | None:
        value = part.get("headers")
        if value is None:
            return None
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in value.items()
        ):
            raise BaasFileExportError("file_export_failed")
        return value
