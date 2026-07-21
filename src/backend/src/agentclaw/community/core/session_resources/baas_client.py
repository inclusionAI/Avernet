"""Known BaaS upload/download URL contract."""
from __future__ import annotations

import logging
import re

from agentclaw.community.core.session_resources.types import DownloadGrant, UploadGrant
from agentclaw.community.plugin_api.http_client import HttpClient

log = logging.getLogger("session_resource.baas")
_SAFE_ROUTE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class SessionResourceBaasClient:
    def __init__(self, http_client: HttpClient) -> None:
        self._http = http_client

    def create_upload_grant(
        self,
        *,
        tenant: str,
        bot_uuid: str,
        device_path: str,
        filename: str,
        expire_seconds: int = 3600,
    ) -> UploadGrant:
        path = self._path(tenant, bot_uuid, "upload-url")
        log.info(
            "session_resource.baas.upload_url.request tenant_hash=%s path_hash=%s",
            self._hash(tenant),
            self._hash(device_path),
        )
        try:
            response = self._http.post(
                path,
                json={
                    "device_path": device_path,
                    "filename": filename,
                    "expire_seconds": expire_seconds,
                },
                timeout=30.0,
            )
        except Exception as exc:
            log.warning(
                "session_resource.baas.upload_url.fail tenant_hash=%s error_type=%s",
                self._hash(tenant),
                type(exc).__name__,
            )
            raise
        data = self._data(response)
        grant = UploadGrant(
            upload_url=self._string(data, "upload_url"),
            transfer_id=self._string(data, "transfer_id"),
            expires_at=self._string(data, "expires_at"),
        )
        log.info(
            "session_resource.baas.upload_url.success tenant_hash=%s transfer_hash=%s",
            self._hash(tenant),
            self._hash(grant.transfer_id),
        )
        return grant

    def create_download_grant(
        self,
        *,
        tenant: str,
        bot_uuid: str,
        device_path: str,
        expire_seconds: int = 600,
    ) -> DownloadGrant:
        log.info(
            "session_resource.baas.download_url.request tenant_hash=%s path_hash=%s",
            self._hash(tenant),
            self._hash(device_path),
        )
        try:
            response = self._http.post(
                self._path(tenant, bot_uuid, "download-url"),
                json={"device_path": device_path, "expire_seconds": expire_seconds},
                timeout=30.0,
            )
        except Exception as exc:
            log.warning(
                "session_resource.baas.download_url.fail tenant_hash=%s error_type=%s",
                self._hash(tenant),
                type(exc).__name__,
            )
            raise
        data = self._data(response)
        grant = DownloadGrant(
            download_url=self._string(data, "download_url"),
            filename=self._string(data, "filename"),
            file_size=int(data.get("file_size", 0)),
            expires_at=self._string(data, "expires_at"),
        )
        log.info(
            "session_resource.baas.download_url.success tenant_hash=%s file_size=%s",
            self._hash(tenant),
            grant.file_size,
        )
        return grant

    @staticmethod
    def _path(tenant: str, bot_uuid: str, operation: str) -> str:
        if not _SAFE_ROUTE_ID.fullmatch(tenant) or not _SAFE_ROUTE_ID.fullmatch(bot_uuid):
            raise ValueError("invalid BaaS route identity")
        return f"/api/v1/bots/{tenant}/{bot_uuid}/files/{operation}"

    @staticmethod
    def _data(response) -> dict:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("error"):
            raise ValueError("BaaS file transfer returned an error")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("BaaS file transfer response is missing data")
        return data

    @staticmethod
    def _string(data: dict, key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"BaaS response is missing {key}")
        return value

    @staticmethod
    def _hash(value: str) -> str:
        from hashlib import sha256

        return sha256(value.encode("utf-8")).hexdigest()[:16]
