"""Skills router HTTP schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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


class PoolCenterMappingIntent(BaseModel):
    """Mapping-v3 Center identity; Runtime owns its physical projection."""

    model_config = ConfigDict(extra="forbid")

    corpus: Literal["center"]
    skill_uuid: str
    sc_version_number: str
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


class ResolvedFilesystemLayoutEvidence(BaseModel):
    """Versioned filesystem facts owned and emitted by Engine Runtime."""

    model_config = ConfigDict(extra="forbid")

    engine: str
    layout_contract_version: str
    active_root: str
    local_root: str
    repo_root: str
    pool_center: str


class RuntimeLayoutProbeEvidence(BaseModel):
    """Extensible probe evidence with a typed resolved-layout member."""

    model_config = ConfigDict(extra="allow")

    resolved_layout: ResolvedFilesystemLayoutEvidence | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


class RuntimeLayoutProbeResponse(BaseModel):
    status: Literal["READY", "NOT_CAPABLE", "TRANSIENT_ERROR", "INVALID"]
    engine: str
    layout_contract_version: str
    preparation_id: str | None
    evidence: RuntimeLayoutProbeEvidence


class RuntimeLayoutProbeApiResponse(ApiResponse):
    data: RuntimeLayoutProbeResponse


class PoolLayoutActivateRequest(BaseModel):
    migration_generation: str
    preparation_id: str
    registered_local_names: list[str]
    mapping_contract_version: str | None = None
    mappings: list[
        PoolSkillMappingIntent | PoolCenterMappingIntent | PoolPhysicalMapping
    ]


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
    mappings: list[
        PoolSkillMappingIntent | PoolCenterMappingIntent | PoolPhysicalMapping
    ]
    retired_mappings: list[
        PoolSkillMappingIntent | PoolCenterMappingIntent | PoolPhysicalMapping
    ] = Field(
        default_factory=list
    )
    source_layout: Literal["pool", "legacy"] = "pool"
    apply_mode: Literal["STRICT", "BEST_EFFORT"] = "STRICT"


__all__ = [
    "BindPathItem",
    "BindPathRequest",
    "CenterEnsureFailureSchema",
    "CenterEnsureItemSchema",
    "CenterEnsureRequestSchema",
    "CenterEnsureResponseSchema",
    "CleanSymlinkRequest",
    "PoolCenterMappingIntent",
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
