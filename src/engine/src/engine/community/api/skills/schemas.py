"""Skills router HTTP schemas."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from engine.community.api.response import ApiResponse


class SymlinkItem(BaseModel):
    source: str
    target: str


class PoolPhysicalMapping(BaseModel):
    """Strict legacy physical mapping shape for Pool endpoints."""

    model_config = ConfigDict(extra="forbid")

    source: str
    target: str


class PoolSkillMappingIntent(BaseModel):
    """Strict mapping-v2 logical shape."""

    model_config = ConfigDict(extra="forbid")

    corpus: Literal["local", "repo"]
    relative_path: str
    link_name: str


class SyncSymlinkRequest(BaseModel):
    symlinks: list[SymlinkItem] | None = None


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


class RuntimeLayoutProbeRequest(BaseModel):
    engine: str
    layout_contract_version: str


class RuntimeLayoutProbeResponse(BaseModel):
    status: Literal["READY", "NOT_CAPABLE", "TRANSIENT_ERROR", "INVALID"]
    engine: str
    layout_contract_version: str
    preparation_id: str | None
    evidence: dict[str, Any]


class RuntimeLayoutProbeApiResponse(ApiResponse):
    data: RuntimeLayoutProbeResponse


class PoolLayoutActivateRequest(BaseModel):
    migration_generation: str
    preparation_id: str
    registered_local_names: list[str]
    mapping_contract_version: str | None = None
    mappings: list[PoolSkillMappingIntent | PoolPhysicalMapping]


class PoolLayoutRollbackRequest(BaseModel):
    rollback_generation: str
    registered_local_names: list[str]


class PoolQuarantineCleanupRequest(BaseModel):
    migration_generation: str


class PoolLayoutActivateResponse(BaseModel):
    committed: bool
    status: Literal[
        "COMMITTED",
        "ALREADY_COMMITTED",
        "ACTIVE_ENTRY_CONFLICT",
        "DATA_INCONSISTENT",
        "INVALID",
        "TRANSIENT_ERROR",
        "POST_CUTOVER_SYNC_PENDING",
        "NOT_ATOMIC",
        "UNKNOWN",
    ]
    evidence: dict[str, Any]


class PoolLayoutActivateApiResponse(ApiResponse):
    data: PoolLayoutActivateResponse


class PoolMappingVerifyRequest(BaseModel):
    mapping_contract_version: str | None = None
    mappings: list[PoolSkillMappingIntent | PoolPhysicalMapping]
    source_layout: Literal["pool", "legacy"] = "pool"


__all__ = [
    "BindPathItem",
    "BindPathRequest",
    "CenterEnsureFailureSchema",
    "CenterEnsureItemSchema",
    "CenterEnsureRequestSchema",
    "CenterEnsureResponseSchema",
    "CleanSymlinkRequest",
    "PoolLayoutActivateApiResponse",
    "PoolLayoutActivateRequest",
    "PoolLayoutActivateResponse",
    "PoolLayoutRollbackRequest",
    "PoolMappingVerifyRequest",
    "PoolPhysicalMapping",
    "PoolQuarantineCleanupRequest",
    "PoolSkillMappingIntent",
    "RuntimeLayoutProbeApiResponse",
    "RuntimeLayoutProbeRequest",
    "RuntimeLayoutProbeResponse",
    "SymlinkItem",
    "SyncSymlinkRequest",
]
