"""Skills Pool operator HTTP schemas."""

from typing import Any

from pydantic import BaseModel, Field

from agentclaw.community.core.skills_pool.recovery_service import (
    ManualRepairResolution,
)
from agentclaw.community.core.skills_pool.operations import RolloutControlGroup


class ApiResponse(BaseModel):
    success: bool
    message: str
    data: Any = None


class FeatureToggleRequest(BaseModel):
    enabled: bool
    reason: str = Field(min_length=1)


class FullRolloutRequest(BaseModel):
    enabled: bool
    engine: str | None = None
    reason: str = Field(min_length=1)


class OwnerFullRolloutRequest(BaseModel):
    owner_id: str = Field(min_length=1)
    engine: str = Field(min_length=1)
    enabled: bool
    acceptance_batch_id: str | None = None
    reason: str = Field(min_length=1)


class EnginePromotionRequest(BaseModel):
    engine: str
    reason: str = Field(min_length=1)
    acceptance_batch_id: str | None = None


class BotIdentityRequest(BaseModel):
    owner_id: str


class WhitelistAddRequest(BotIdentityRequest):
    bot_id: str
    batch_id: str = Field(min_length=1)
    acceptance_batch_id: str | None = None
    reason: str = Field(min_length=1)


class WhitelistRemoveRequest(BotIdentityRequest):
    bot_id: str
    reason: str = Field(min_length=1)


class ControlBotRequest(BotIdentityRequest):
    bot_id: str
    batch_id: str = Field(min_length=1)
    group: RolloutControlGroup
    present: bool = True
    reason: str = Field(min_length=1)


class BatchAcceptanceRequest(BaseModel):
    engine: str
    batch_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RepairRequest(BotIdentityRequest):
    migration_generation: str
    note: str = Field(min_length=1)
    resolution: ManualRepairResolution


class RollbackRequest(BotIdentityRequest):
    rollback_generation: str
    note: str = Field(min_length=1)
