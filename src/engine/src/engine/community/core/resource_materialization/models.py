"""Transport-neutral value types for resource materialization."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from urllib.parse import urlsplit


def hash_identifier(value: str) -> str:
    """Return the stable non-reversible identifier used in workspace paths."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class MaterializationRequest(BaseModel):
    resource_id: str = Field(min_length=1, max_length=128)
    transfer_id: str = Field(min_length=1, max_length=256)
    task_id: str = Field(min_length=1, max_length=128)
    task_version: int = Field(ge=1)
    scope_key_hash: str = Field(min_length=1, max_length=128)
    session_key_hash: str = Field(min_length=1, max_length=128)
    transfer_api_version: Literal["session_v2", "bot_device_v1"] = "bot_device_v1"
    tenant: str | None = Field(default=None, min_length=1, max_length=128)
    session_id: str | None = Field(default=None, min_length=1, max_length=2048)
    workspace_relative_path: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
    )
    device_path: str | None = Field(default=None, min_length=1, max_length=2048)
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int | None = Field(default=None, ge=0)
    content_hash: str | None = Field(default=None, min_length=1, max_length=128)
    uploaded_at: datetime | None = None

    def model_post_init(self, __context, /) -> None:
        if self.transfer_api_version == "session_v2":
            if (
                not self.tenant
                or not self.session_id
                or not self.workspace_relative_path
            ):
                raise ValueError(
                    "session_v2 requires tenant, session_id, and workspace_relative_path"
                )
        elif not self.device_path:
            raise ValueError("bot_device_v1 requires device_path")


class ChatAttachmentMaterializationRequest(BaseModel):
    """Internal request for a short-lived chat attachment capability."""

    attachment_id: str = Field(min_length=1, max_length=128)
    session_key: str = Field(min_length=1, max_length=2048)
    filename: str = Field(min_length=1, max_length=255)
    temporary_url: str = Field(min_length=1, max_length=8192)
    scope_key_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expires_at_ms: int | None = Field(default=None, ge=0)
    size_bytes: int | None = Field(default=None, ge=0)
    content_hash: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")

    @field_validator("temporary_url")
    @classmethod
    def validate_temporary_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("temporary_url must be an HTTP or HTTPS URL without userinfo")
        return value


class MaterializationResult(BaseModel):
    resource_id: str
    transfer_id: str
    task_id: str
    task_version: int
    ready: bool
    canonical_bot_absolute_path: str | None = None
    relative_path: str | None = None
    size_bytes: int | None = None
    content_hash: str | None = None
    error_code: str | None = None


class ManifestEntry(BaseModel):
    resource_id: str
    transfer_id: str
    task_id: str
    task_version: int
    scope_key_hash: str
    session_key_hash: str
    filename: str
    relative_path: str
    canonical_bot_absolute_path: str
    size_bytes: int
    content_hash: str
    status: Literal["ready", "failed"]
    observed_size: int | None = None
    observed_mtime_ns: int | None = None
    observed_inode: int | None = None
    uploaded_at: datetime | None = None
    baas_tenant: str | None = None
    source_kind: Literal["baas_session_file", "temporary_url"] = "baas_session_file"
    source_attachment_id: str | None = None
    source_url_hash: str | None = None


@dataclass(frozen=True)
class MaterializedContent:
    """Controlled metadata for a file resolved from a ready manifest entry."""

    path: Path
    filename: str
    media_type: str
    content_disposition: str
    size_bytes: int
