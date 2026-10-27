"""Wire contracts for Bot-public risk checks, shared by both HTTP surfaces."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=1, max_length=128, description="Stable identifier for retries of this request.")


class LegacyRiskRequest(_Request):
    public: Literal["1"] = Field(description="Request publication; withdrawal uses the existing public endpoint.")
    permission_owner: Literal["caller", "owner"] = "caller"
    friend_approval: Literal["0", "1"] = "0"
    owner_id: str | None = Field(default=None, min_length=1)

    @field_validator("public", "friend_approval", mode="before")
    @classmethod
    def normalize_number(cls, value):
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value


class ViewDept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deptNo: str = Field(min_length=1, description="Canonical department ID.")
    deptName: str = Field(min_length=1, description="Department display name.")


class OpenApiRiskRequest(_Request):
    public_scope: Literal["user", "agent"]
    visibility: Literal["public", "protected"]
    view_depts: list[ViewDept] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_visibility_scope(self):
        if self.visibility == "protected" and not self.view_depts:
            raise ValueError("protected visibility requires at least one department")
        if self.visibility == "public" and self.view_depts:
            raise ValueError("public visibility does not accept departments")
        return self


class RiskTarget(BaseModel):
    """Only public target fields are returned, never internal workflow input."""
    model_config = ConfigDict(extra="ignore")
    public_scope: str
    visibility: str
    view_depts: list[ViewDept] = Field(default_factory=list)
    permission_owner: str | None = None
    friend_approval: str | None = None


class RiskRecordResponse(BaseModel):
    """Public result: the security report detail remains on the external system."""
    model_config = ConfigDict(extra="ignore")
    risk_check_id: str
    phase: str
    security_status: str | None = None
    detail_url: str | None = None
    auto_poll_paused: bool = False
    auto_poll_until: datetime | None = None
    next_action_at: datetime | None = None
    target: RiskTarget
    error_reason: str | None = None
    invalidation_reason: str | None = None
    approval_puid: str | None = None


class RiskRefreshResponse(BaseModel):
    outcome: Literal["UPDATED", "NOT_QUERIED"]
    record: RiskRecordResponse
