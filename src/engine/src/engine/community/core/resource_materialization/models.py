"""Transport-neutral value types for resource materialization."""
from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field


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
    device_path: str = Field(min_length=1, max_length=2048)
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int | None = Field(default=None, ge=0)
    content_hash: str | None = Field(default=None, min_length=1, max_length=128)


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
