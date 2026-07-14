"""
Worker Management API Schemas

Public-safe request/response models for worker management routes.
Aligned with original contract for worker batch, sync, availability, trust-level, config routes.

S28B-2B-12: Public-safe contract models for route skeletons.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Availability(str, Enum):
    """Worker availability/visibility status."""
    PRIVATE = "private"
    PROTECTED = "protected"
    PUBLIC = "public"


class TrustLevel(str, Enum):
    """Worker trust level."""
    UNVERIFIED = "unverified"
    VERIFYING = "verifying"
    SANDBOX_ONLY = "sandbox_only"
    GUARDED = "guarded"
    TRUSTED = "trusted"


# =============================================================================
# Worker Batch Query Schemas
# =============================================================================

class WorkerBatchQueryRequest(BaseModel):
    """
    Worker batch query request.

    Query multiple workers by their IDs.

    Attributes:
        worker_ids: List of worker IDs to query
    """

    worker_ids: list[str] = Field(
        min_length=1,
        max_length=100,
        description="List of worker IDs to query",
    )


class WorkerBatchQueryResponse(BaseModel):
    """
    Worker batch query response.

    Attributes:
        workers: List of worker data
        not_found_ids: Worker IDs not found
        total: Total workers found
    """

    workers: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of worker data",
    )

    not_found_ids: list[str] = Field(
        default_factory=list,
        description="Worker IDs not found",
    )

    total: int = Field(
        default=0,
        ge=0,
        description="Total workers found",
    )


# =============================================================================
# Worker Sync Schemas (Original Root Contract)
# =============================================================================

class SummaryData(BaseModel):
    """Summary data within a sync profile."""
    capability: Optional[str] = Field(None, description="Capability summary")
    role: Optional[str] = Field(None, description="Role summary")


class SyncProfileData(BaseModel):
    """
    Profile data within a sync request.

    Aligned with original root contract (worker_routes.py::SyncProfileData).
    """
    profile_id: str = Field(
        default="default",
        description="Profile ID, defaults to 'default'",
        min_length=1,
    )
    display_name: Optional[str] = Field(
        default=None,
        description="Display name",
    )
    soul_md: Optional[str] = Field(
        default=None,
        description="SOUL.md content",
    )
    contents: dict[str, Any] = Field(
        default_factory=dict,
        description="Extended content map, supports any type",
    )
    skill_sets: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Skill sets",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Extended metadata",
    )
    summary: Optional[SummaryData] = Field(
        default=None,
        description="Summary info including capability and role",
    )
    activate: bool = Field(
        default=True,
        description="Whether to activate profile",
    )


class WorkerSyncRequest(BaseModel):
    """
    Worker sync request.

    Atomic sync operation: create/update worker + set online + upsert profile.
    Sent by BCS during bot onboard. Idempotent — safe to call repeatedly.

    Aligned with original root contract (worker_routes.py::SyncWorkerRequest).
    """
    type: str = Field(
        default="bot",
        description="Worker type",
    )
    name: str = Field(
        ...,
        description="Display name",
        min_length=1,
    )
    description: Optional[str] = Field(
        default=None,
        description="Description",
    )
    responsibilities: list[str] = Field(
        default_factory=lambda: ["general"],
        description="Responsibilities",
    )
    domains: list[str] = Field(
        default_factory=list,
        description="Domains",
    )
    runtime_state: Optional[str] = Field(
        default=None,
        description="Runtime state (online/offline). None means no change",
    )
    capabilities: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Capabilities",
    )
    skills: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Skills",
    )
    availability: str = Field(
        default="protected",
        description="Visibility (private/protected/public)",
    )
    trust_level: str = Field(
        default="guarded",
        description="Trust level",
    )
    profile_key: Optional[str] = Field(
        default=None,
        description="Profile key",
    )
    profile: SyncProfileData = Field(
        ...,
        description="Profile data",
    )
    sync_llm: bool = Field(
        default=False,
        description="Whether to call LLM analysis synchronously. True=wait, False=async",
    )


class WorkerSyncResponse(BaseModel):
    """
    Worker sync response (canonical schema aligned with root_original).

    Atomic sync operation response: create/update worker + set online + upsert profile.

    Attributes:
        success: Operation success flag (always True for successful sync)
        worker_id: Created/updated worker ID
        created: True if worker was newly created, False if updated
        runtime_state: Runtime state after sync ("online" or None if not modified)
        profile_id: Profile ID that was synced
        profile_activated: True if profile was activated
    """

    success: bool = Field(
        description="Operation success flag",
    )

    worker_id: str = Field(
        description="Created/updated worker ID",
    )

    created: bool = Field(
        description="True if worker was newly created, False if updated",
    )

    runtime_state: Optional[str] = Field(
        default=None,
        description="运行态，None 表示未修改",
    )

    profile_id: str = Field(
        description="Profile ID that was synced",
    )

    profile_activated: bool = Field(
        description="True if profile was activated",
    )


# =============================================================================
# Worker Availability Schemas
# =============================================================================

class WorkerAvailabilityUpdate(BaseModel):
    """
    Worker availability update request.

    Attributes:
        availability: Availability status (private, protected, public)
    """

    availability: Availability = Field(
        description="Availability status (private, protected, public)",
    )


class WorkerAvailabilityResponse(BaseModel):
    """
    Worker availability update response.

    Attributes:
        worker_id: Worker ID
        availability: Updated availability
        updated_at: Update timestamp
    """

    worker_id: str = Field(
        description="Worker ID",
    )

    availability: Availability = Field(
        description="Updated availability",
    )

    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Update timestamp",
    )


# =============================================================================
# Worker Trust Level Schemas
# =============================================================================

class WorkerTrustLevelUpdate(BaseModel):
    """
    Worker trust level update request.

    Attributes:
        trust_level: Trust level (unverified, verifying, sandbox_only, guarded, trusted)
    """

    trust_level: TrustLevel = Field(
        description="Trust level (unverified, verifying, sandbox_only, guarded, trusted)",
    )


class WorkerTrustLevelResponse(BaseModel):
    """
    Worker trust level update response.

    Attributes:
        worker_id: Worker ID
        trust_level: Updated trust level
        updated_at: Update timestamp
    """

    worker_id: str = Field(
        description="Worker ID",
    )

    trust_level: TrustLevel = Field(
        description="Updated trust level",
    )

    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Update timestamp",
    )


# =============================================================================
# Worker Patch Schemas
# =============================================================================

class WorkerPatchRequest(BaseModel):
    """
    Worker partial update request.

    Attributes:
        name: Worker name
        description: Worker description
        domains: Worker domain tags
        capabilities: Worker capabilities
        responsibilities: Worker responsibilities
        metadata: Additional metadata
    """

    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="Worker name",
    )

    description: Optional[str] = Field(
        default=None,
        description="Worker description",
    )

    domains: Optional[list[str]] = Field(
        default=None,
        description="Worker domain tags",
    )

    capabilities: Optional[list[str]] = Field(
        default=None,
        description="Worker capabilities",
    )

    responsibilities: Optional[list[str]] = Field(
        default=None,
        description="Worker responsibilities",
    )

    metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="Additional metadata",
    )


class WorkerPatchResponse(BaseModel):
    """
    Worker patch response.

    Attributes:
        worker_id: Worker ID
        updated_fields: List of updated fields
        updated_at: Update timestamp
        version: New version number
    """

    worker_id: str = Field(
        description="Worker ID",
    )

    updated_fields: list[str] = Field(
        default_factory=list,
        description="List of updated fields",
    )

    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Update timestamp",
    )

    version: int = Field(
        default=1,
        ge=1,
        description="New version number",
    )


# =============================================================================
# Worker Config Schemas
# =============================================================================

class WorkerConfigResponse(BaseModel):
    """
    Worker config response.

    Attributes:
        worker_id: Worker ID
        fusion_enable: Whether fusion is enabled
        config: Full configuration
        version: Config version
        updated_at: Last update timestamp
    """

    worker_id: str = Field(
        description="Worker ID",
    )

    fusion_enable: bool = Field(
        default=False,
        description="Whether worker can participate in profile fusion",
    )

    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Full configuration",
    )

    version: int = Field(
        default=1,
        ge=1,
        description="Config version",
    )

    updated_at: Optional[datetime] = Field(
        default=None,
        description="Last update timestamp",
    )


class WorkerConfigUpdate(BaseModel):
    """
    Worker config update request.

    Attributes:
        fusion_enable: Whether to enable fusion
        config: Additional configuration options
    """

    fusion_enable: Optional[bool] = Field(
        default=None,
        description="Whether worker can participate in profile fusion",
    )

    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional configuration options",
    )


class WorkerConfigBatchUpdate(BaseModel):
    """
    Worker config batch update request.

    Attributes:
        updates: List of worker config updates
    """

    updates: list[dict[str, Any]] = Field(
        min_length=1,
        max_length=100,
        description="List of config updates (each with worker_id and config)",
    )


class WorkerConfigBatchResponse(BaseModel):
    """
    Worker config batch update response.

    Attributes:
        updated: Updated worker IDs
        failed: Failed worker IDs with reasons
        total: Total processed
    """

    updated: list[str] = Field(
        default_factory=list,
        description="Updated worker IDs",
    )

    failed: list[dict[str, str]] = Field(
        default_factory=list,
        description="Failed worker IDs with reasons",
    )

    total: int = Field(
        default=0,
        ge=0,
        description="Total processed",
    )


class WorkersBySourceResponse(BaseModel):
    """
    Workers by source response.

    Attributes:
        source: Source type
        workers: List of workers
        total: Total count
    """

    source: str = Field(
        description="Source type",
    )

    workers: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of workers",
    )

    total: int = Field(
        default=0,
        ge=0,
        description="Total count",
    )


# =============================================================================
# Worker Profile Quality Schemas
# =============================================================================

class WorkerProfileQualityResponse(BaseModel):
    """
    Worker profile quality response.

    Attributes:
        worker_id: Worker ID
        quality_score: Overall quality score (0-1)
        profile_count: Number of profiles
        active_profile_key: Active profile key
        quality_details: Quality breakdown
    """

    worker_id: str = Field(
        description="Worker ID",
    )

    quality_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Overall quality score (0-1)",
    )

    profile_count: int = Field(
        default=0,
        ge=0,
        description="Number of profiles",
    )

    active_profile_key: Optional[str] = Field(
        default=None,
        description="Active profile key",
    )

    quality_details: dict[str, Any] = Field(
        default_factory=dict,
        description="Quality breakdown",
    )


# =============================================================================
# Profile Request/Response Schemas (OpenAPI Contract Aligned)
# =============================================================================

class SkillSetRequest(BaseModel):
    """
    Skill set request schema.

    Attributes:
        name: Skill name (required)
        description: Skill description
        content: Skill detailed content
        metadata: Skill metadata
    """

    name: str = Field(
        ...,
        min_length=1,
        description="Skill name",
    )

    description: Optional[str] = Field(
        default=None,
        description="Skill description",
    )

    content: Optional[str] = Field(
        default=None,
        description="Skill detailed content",
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Skill metadata",
    )


class ProfileRequest(BaseModel):
    """
    Profile create/update request (PUT /v1/workers/{worker_id}/profiles/{profile_id}).

    Aligned with OpenAPI contract: all fields are optional for upsert.

    Attributes:
        display_name: Display name
        soul_md: SOUL.md content - core identity
        agents_md: AGENTS.md content - work configuration
        tools_md: TOOLS.md content - tool configuration
        boot_md: BOOT.md content - boot configuration
        heartbeat_md: HEARTBEAT.md content
        contents: Extended content map (supports any type)
        skill_sets: Skill sets
        metadata: Extended metadata
        activate: Whether to set as active profile
    """

    display_name: Optional[str] = Field(
        default=None,
        description="Display name",
    )

    soul_md: Optional[str] = Field(
        default=None,
        description="SOUL.md content - core identity",
    )

    agents_md: Optional[str] = Field(
        default=None,
        description="AGENTS.md content - work configuration",
    )

    tools_md: Optional[str] = Field(
        default=None,
        description="TOOLS.md content - tool configuration",
    )

    boot_md: Optional[str] = Field(
        default=None,
        description="BOOT.md content - boot configuration",
    )

    heartbeat_md: Optional[str] = Field(
        default=None,
        description="HEARTBEAT.md content",
    )

    contents: dict[str, Any] = Field(
        default_factory=dict,
        description="Extended content map, supports any type",
    )

    skill_sets: list[SkillSetRequest] = Field(
        default_factory=list,
        description="Skill sets",
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Extended metadata",
    )

    activate: bool = Field(
        default=False,
        description="Whether to set as active profile",
    )


class ProfilePatchRequest(BaseModel):
    """
    Profile partial update request (PATCH /v1/workers/{worker_id}/profiles/{profile_id}).

    Only updates provided fields, unprovided fields remain unchanged.
    - contents: Merge update, provided keys are added or replaced
    - contents_delete: Delete specified contents keys
    - metadata: Merge update, similar to contents
    - metadata_delete: Delete specified metadata keys
    - skill_sets: If provided, replace all (cannot partial update)

    Attributes:
        display_name: Display name (optional)
        soul_md: SOUL.md content (optional)
        agents_md: AGENTS.md content (optional)
        tools_md: TOOLS.md content (optional)
        boot_md: BOOT.md content (optional)
        heartbeat_md: HEARTBEAT.md content (optional)
        contents: Contents to merge (optional)
        contents_delete: Contents keys to delete (optional)
        skill_sets: Skill sets to replace (optional)
        metadata: Metadata to merge (optional)
        metadata_delete: Metadata keys to delete (optional)
        activate: Whether to activate profile (optional)
    """

    display_name: Optional[str] = Field(
        default=None,
        description="Display name (unchanged if not provided)",
    )

    soul_md: Optional[str] = Field(
        default=None,
        description="SOUL.md content (unchanged if not provided)",
    )

    agents_md: Optional[str] = Field(
        default=None,
        description="AGENTS.md content (unchanged if not provided)",
    )

    tools_md: Optional[str] = Field(
        default=None,
        description="TOOLS.md content (unchanged if not provided)",
    )

    boot_md: Optional[str] = Field(
        default=None,
        description="BOOT.md content (unchanged if not provided)",
    )

    heartbeat_md: Optional[str] = Field(
        default=None,
        description="HEARTBEAT.md content (unchanged if not provided)",
    )

    contents: Optional[dict[str, Any]] = Field(
        default=None,
        description="Contents to merge (provided keys are added or replaced)",
    )

    contents_delete: Optional[list[str]] = Field(
        default=None,
        description="Contents keys to delete",
    )

    skill_sets: Optional[list[SkillSetRequest]] = Field(
        default=None,
        description="Skill sets to replace (if provided, replace all)",
    )

    metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="Metadata to merge (provided keys are added or replaced)",
    )

    metadata_delete: Optional[list[str]] = Field(
        default=None,
        description="Metadata keys to delete",
    )

    activate: bool = Field(
        default=False,
        description="Whether to activate profile",
    )


class ProfileResponse(BaseModel):
    """
    Profile response schema.

    All fields except is_active, version, content_type are nullable per OpenAPI contract.

    Attributes:
        worker_id: Worker ID
        profile_id: Profile ID
        display_name: Display name (nullable)
        soul_md: SOUL.md content (nullable)
        agents_md: AGENTS.md content (nullable)
        tools_md: TOOLS.md content (nullable)
        boot_md: BOOT.md content (nullable)
        heartbeat_md: HEARTBEAT.md content (nullable)
        contents: Extended content map
        skill_sets: Skill sets
        metadata: Metadata
        content_type: Content type
        is_active: Whether profile is active
        version: Profile version
        quality_score: Quality score (optional)
        quality_issues: Quality issues
        created_at: Creation timestamp (nullable)
        updated_at: Update timestamp (nullable)
    """

    worker_id: str = Field(
        description="Worker ID",
    )

    profile_id: str = Field(
        description="Profile ID",
    )

    display_name: Optional[str] = Field(
        default=None,
        description="Display name",
    )

    soul_md: Optional[str] = Field(
        default=None,
        description="SOUL.md content",
    )

    agents_md: Optional[str] = Field(
        default=None,
        description="AGENTS.md content",
    )

    tools_md: Optional[str] = Field(
        default=None,
        description="TOOLS.md content",
    )

    boot_md: Optional[str] = Field(
        default=None,
        description="BOOT.md content",
    )

    heartbeat_md: Optional[str] = Field(
        default=None,
        description="HEARTBEAT.md content",
    )

    contents: dict[str, Any] = Field(
        default_factory=dict,
        description="Extended content map",
    )

    skill_sets: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Skill sets",
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata",
    )

    content_type: str = Field(
        default="api",
        description="Content type",
    )

    is_active: bool = Field(
        default=False,
        description="Whether profile is active",
    )

    version: int = Field(
        default=1,
        ge=1,
        description="Profile version",
    )

    quality_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Quality score (0-1)",
    )

    quality_issues: list[str] = Field(
        default_factory=list,
        description="Quality issues",
    )

    created_at: Optional[str] = Field(
        default=None,
        description="Creation timestamp",
    )

    updated_at: Optional[str] = Field(
        default=None,
        description="Update timestamp",
    )


__all__ = [
    "Availability",
    "TrustLevel",
    "WorkerBatchQueryRequest",
    "WorkerBatchQueryResponse",
    "WorkerSyncRequest",
    "WorkerSyncResponse",
    "WorkerAvailabilityUpdate",
    "WorkerAvailabilityResponse",
    "WorkerTrustLevelUpdate",
    "WorkerTrustLevelResponse",
    "WorkerPatchRequest",
    "WorkerPatchResponse",
    "WorkerConfigResponse",
    "WorkerConfigUpdate",
    "WorkerConfigBatchUpdate",
    "WorkerConfigBatchResponse",
    "WorkersBySourceResponse",
    "WorkerProfileQualityResponse",
    "SkillSetRequest",
    "ProfileRequest",
    "ProfilePatchRequest",
    "ProfileResponse",
]