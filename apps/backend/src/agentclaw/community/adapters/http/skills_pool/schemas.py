"""Skills Pool operator HTTP schemas."""

from typing import Any

from pydantic import BaseModel, Field

from agentclaw.community.core.skills_pool.recovery_service import (
    ManualRepairResolution,
)


class ApiResponse(BaseModel):
    success: bool
    message: str
    data: Any = None


class PolicyMutationRequest(BaseModel):
    expected_revision: str | None
    reason: str = Field(min_length=1)


class FeatureToggleRequest(PolicyMutationRequest):
    enabled: bool


class EngineAdmissionRequest(PolicyMutationRequest):
    enabled: bool


class BotPolicyRequest(PolicyMutationRequest):
    owner_id: str = Field(min_length=1)
    engine: str = Field(min_length=1)


class OwnerPolicyRequest(PolicyMutationRequest):
    engine: str = Field(min_length=1)


class BotIdentityRequest(BaseModel):
    owner_id: str


class RepairRequest(BotIdentityRequest):
    migration_generation: str
    note: str = Field(min_length=1)
    resolution: ManualRepairResolution


class RollbackRequest(BotIdentityRequest):
    rollback_generation: str
    note: str = Field(min_length=1)
