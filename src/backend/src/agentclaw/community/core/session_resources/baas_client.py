"""BaaS Session File Sharing and legacy transfer control-plane client."""
from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from agentclaw.community.core.session_resources.types import UploadGrant
from agentclaw.community.plugin_api.http_client import HttpClient

log = logging.getLogger("session_resource.baas")


class SessionFileUpstreamUnavailableError(ValueError):
    def __init__(self) -> None:
        super().__init__("session_file_upstream_unavailable")


class SessionResourceBaasClient:
    def __init__(self, http_client: HttpClient) -> None:
        self._http = http_client

    def create_session_upload_grant(
        self,
        *,
        tenant: str,
        session_id: str,
        filename: str,
        file_size: int | None,
        operator: str,
        expire_seconds: int = 3600,
    ) -> UploadGrant:
        log.info(
            "session_resource.baas.session_upload_url.request tenant_hash=%s session_hash=%s",
            self._hash(tenant),
            self._hash(session_id),
        )
        path = self._session_path(tenant, session_id, "files/upload-url")
        try:
            response = self._http.post(
                path,
                json={
                    "filename": filename,
                    "file_size": file_size or 0,
                    "operator": operator,
                    "expire_seconds": expire_seconds,
                },
                timeout=30.0,
            )
            data = self._data(response)
            grant = UploadGrant(
                transfer_id=self._string(data, "transfer_id"),
                upload_type=self._string(data, "type"),
                upload_url=self._optional_string(data, "upload_url"),
                http_method=self._optional_string(data, "http_method") or "PUT",
                expires_at=self._optional_string(data, "expires_at"),
                upload_session_id=self._optional_string(data, "upload_session_id"),
                part_size=self._optional_int(data, "part_size"),
                part_count=self._optional_int(data, "part_count"),
                parts=self._optional_parts(data),
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise self._upstream_error(
                operation="session_upload_url",
                tenant=tenant,
                reference=session_id,
                exc=exc,
            ) from exc
        log.info(
            "session_resource.baas.session_upload_url.success tenant_hash=%s transfer_hash=%s upload_type=%s",
            self._hash(tenant),
            self._hash(grant.transfer_id),
            grant.upload_type,
        )
        return grant

    def complete_session_upload(
        self,
        *,
        tenant: str,
        session_id: str,
        transfer_id: str,
    ) -> str:
        log.info(
            "session_resource.baas.session_upload_complete.request tenant_hash=%s transfer_hash=%s",
            self._hash(tenant),
            self._hash(transfer_id),
        )
        path = self._session_path(
            tenant,
            session_id,
            f"files/upload-url/{self._segment(transfer_id)}/complete",
        )
        try:
            response = self._http.post(
                path,
                json=None,
                timeout=30.0,
            )
            status = self._string(self._data(response), "status")
        except (httpx.HTTPError, ValueError) as exc:
            raise self._upstream_error(
                operation="session_upload_complete",
                tenant=tenant,
                reference=transfer_id,
                exc=exc,
            ) from exc
        log.info(
            "session_resource.baas.session_upload_complete.success tenant_hash=%s transfer_hash=%s status=%s",
            self._hash(tenant),
            self._hash(transfer_id),
            status,
        )
        return status

    def complete_legacy_upload(
        self,
        *,
        tenant: str,
        bot_uuid: str,
        transfer_id: str,
    ) -> str:
        """Complete a record created before the Session File Sharing API."""
        log.info(
            "session_resource.baas.legacy_upload_complete.request tenant_hash=%s transfer_hash=%s",
            self._hash(tenant),
            self._hash(transfer_id),
        )
        path = self._legacy_path(
            tenant,
            bot_uuid,
            f"upload-url/{self._segment(transfer_id)}/complete",
        )
        try:
            response = self._http.post(
                path,
                json=None,
                timeout=30.0,
            )
            status = self._string(self._data(response), "status")
        except (httpx.HTTPError, ValueError) as exc:
            raise self._upstream_error(
                operation="legacy_upload_complete",
                tenant=tenant,
                reference=transfer_id,
                exc=exc,
            ) from exc
        log.info(
            "session_resource.baas.legacy_upload_complete.success tenant_hash=%s transfer_hash=%s status=%s",
            self._hash(tenant),
            self._hash(transfer_id),
            status,
        )
        return status

    @staticmethod
    def _session_path(tenant: str, session_id: str, operation: str) -> str:
        return "/api/v1/sessions/{}/{}/{}".format(
            SessionResourceBaasClient._segment(tenant),
            SessionResourceBaasClient._segment(session_id),
            operation,
        )

    @staticmethod
    def _legacy_path(tenant: str, bot_uuid: str, operation: str) -> str:
        return "/api/v1/bots/{}/{}/files/{}".format(
            SessionResourceBaasClient._segment(tenant),
            SessionResourceBaasClient._segment(bot_uuid),
            operation,
        )

    @staticmethod
    def _segment(value: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("invalid BaaS route identity")
        return quote(value, safe="")

    @staticmethod
    def _data(response) -> dict:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("error"):
            raise ValueError("BaaS file transfer returned an error")
        if payload.get("code") not in {None, 0}:
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
    def _optional_string(data: dict, key: str) -> str | None:
        value = data.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise ValueError(f"BaaS response has invalid {key}")
        return value

    @staticmethod
    def _optional_int(data: dict, key: str) -> int | None:
        value = data.get(key)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"BaaS response has invalid {key}")
        return value

    @staticmethod
    def _optional_parts(data: dict) -> list[dict] | None:
        value = data.get("parts")
        if value is None:
            return None
        if not isinstance(value, list) or not all(isinstance(part, dict) for part in value):
            raise ValueError("BaaS response has invalid parts")
        return value

    @classmethod
    def _upstream_error(
        cls,
        *,
        operation: str,
        tenant: str,
        reference: str,
        exc: BaseException,
    ) -> SessionFileUpstreamUnavailableError:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        log.warning(
            "session_resource.baas.%s.fail tenant_hash=%s reference_hash=%s error_type=%s upstream_status=%s",
            operation,
            cls._hash(tenant),
            cls._hash(reference),
            type(exc).__name__,
            status_code if isinstance(status_code, int) else None,
        )
        return SessionFileUpstreamUnavailableError()

    @staticmethod
    def _hash(value: str) -> str:
        from hashlib import sha256

        return sha256(value.encode("utf-8")).hexdigest()[:16]
