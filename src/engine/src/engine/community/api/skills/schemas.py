"""Skills router HTTP schemas."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class SymlinkItem(BaseModel):
    source: str
    target: str


class SyncSymlinkRequest(BaseModel):
    symlinks: Optional[list[SymlinkItem]] = None


class CleanSymlinkRequest(BaseModel):
    directories: list[str]


class BindPathItem(BaseModel):
    source: str
    target: str


class BindPathRequest(BaseModel):
    symlinks: list[BindPathItem]
    clean_target_dir: bool = True


class CenterEnsureItemSchema(BaseModel):
    skill_uuid: str
    version: str


class CenterEnsureRequestSchema(BaseModel):
    items: list[CenterEnsureItemSchema]


class CenterEnsureFailureSchema(BaseModel):
    skill_uuid: str
    version: str
    reason: str


class CenterEnsureResponseSchema(BaseModel):
    ok: list[CenterEnsureItemSchema]
    failed: list[CenterEnsureFailureSchema]


__all__ = [
    "SymlinkItem",
    "SyncSymlinkRequest",
    "CleanSymlinkRequest",
    "BindPathItem",
    "BindPathRequest",
    "CenterEnsureItemSchema",
    "CenterEnsureRequestSchema",
    "CenterEnsureFailureSchema",
    "CenterEnsureResponseSchema",
]
