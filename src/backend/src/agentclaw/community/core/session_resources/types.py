"""Domain value types for session resources."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


def hash_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class SessionResourceStatus(str, Enum):
    UPLOAD_URL_ISSUED = "upload_url_issued"
    DEVICE_SYNCING = "device_syncing"
    READY = "ready"
    DEVICE_SYNC_FAILED = "device_sync_failed"
    DELETED = "deleted"


@dataclass(frozen=True)
class SessionResourceRecord:
    resource_id: str
    owner_id: str
    bot_id: str
    scope_type: str
    scope_key_hash: str
    session_key_hash: str
    engine_type: str
    tenant: str
    bot_uuid: str
    display_name: str
    filename: str
    device_path: str
    workspace_relative_path: str
    transfer_id: str
    status: SessionResourceStatus
    task_id: str | None = None
    task_version: int = 0
    size_bytes: int | None = None
    client_content_hash: str | None = None
    materialized_ref: dict | None = None
    error_code: str | None = None
    deleted_at: datetime | None = None
    gmt_create: datetime | None = None
    gmt_modified: datetime | None = None


@dataclass(frozen=True)
class UploadGrant:
    upload_url: str
    transfer_id: str
    expires_at: str


@dataclass(frozen=True)
class DownloadGrant:
    download_url: str
    filename: str
    file_size: int
    expires_at: str


@dataclass(frozen=True)
class SessionUploadIntent:
    resource: SessionResourceRecord
    grant: UploadGrant
