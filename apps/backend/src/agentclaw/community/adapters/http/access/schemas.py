"""Access control & user management — Pydantic Request/Response models."""
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一的 API 响应格式"""
    success: bool
    message: str
    error_code: int = 200
    data: T | None = None


# ==================== Request Models ====================


class UpsertUserRequest(BaseModel):
    user_id: str = Field(..., description="User ID (staff number)")
    user_type: str = Field("COMPETE", description="User type: NORMAL / COMPETE")
    status: str = Field("ACCESS", description="User status: ACCESS / REFUSE")


class AllowRequest(BaseModel):
    entity_id: str = Field(..., description="Entity ID to add/remove from whitelist")
    entity_type: Literal["staff"] = Field("staff", description="Entity type")


class SetBotsCeilingRequest(BaseModel):
    entity_id: str = Field(..., description="User staff ID to set ceiling for")
    ceiling: int = Field(..., gt=0, description="BOT count ceiling (must be > 0)")


class SetSpaceBotsCeilingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ceiling: int = Field(..., gt=0, description="Team Space BOT count ceiling")


class UserListCorrectionRequest(BaseModel):
    """One authenticated correction of a current-environment user-list entry."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(..., min_length=1, max_length=1024)
    user_list_type: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    in_whitelist: bool


# ==================== Response Data Models ====================


class UserItem(BaseModel):
    id: int = Field(..., description="Record primary key")
    user_id: str = Field(..., alias="userId", description="User ID")
    user_type: str = Field(..., alias="userType", description="User type")
    status: str = Field(..., description="User status")
    gmt_create: str | None = Field(None, alias="gmtCreate", description="Creation time")
    gmt_modified: str | None = Field(None, alias="gmtModified", description="Last modification time")

    model_config = {"populate_by_name": True}


class WhitelistCheckData(BaseModel):
    label: int = Field(..., description="1 = allowed, 0 = denied")
    operator: str = Field(..., description="Operator name")
    staff_no: str = Field(..., alias="staffNo", description="Staff number")

    model_config = {"populate_by_name": True}


class UserListCheckData(BaseModel):
    """Minimal boolean result used by the frontend rollout gate."""

    in_whitelist: bool


class AllowDisallowData(BaseModel):
    entity_id: str = Field(..., description="Entity ID")
    entity_type: str = Field(..., description="Entity type")


class QuotaData(BaseModel):
    quota: int = Field(..., description="Daily container quota")
    total_limit: int = Field(..., alias="totalLimit", description="Total container limit")
    active_count: int = Field(..., alias="activeCount", description="Currently active devices")
    effective_quota: int = Field(..., alias="effectiveQuota", description="Effective quota after constraints")
    update_time: str = Field(..., alias="updateTime", description="Daily update time (HH:MM)")

    model_config = {"populate_by_name": True}
