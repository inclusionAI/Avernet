"""Wire contracts for Bot-public risk checks, shared by both HTTP surfaces."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(
        min_length=1,
        max_length=128,
        description="Stable identifier for retries of this request.",
    )


class LegacyRiskRequest(_Request):
    public: Literal["1"] = Field(
        description="Request publication; withdrawal uses the existing public endpoint."
    )
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
    """Department included in an authorized publication target."""

    model_config = ConfigDict(extra="forbid")
    deptNo: str = Field(min_length=1, description="Canonical department ID.")
    deptName: str = Field(min_length=1, description="Department display name.")


class OpenApiRiskRequest(_Request):
    """Submit a new public risk application or replay the same request."""

    public_scope: Literal["user", "agent"] = Field(description="Publication dimension.")
    visibility: Literal["public", "protected"] = Field(description="Target visibility.")
    view_depts: list[ViewDept] = Field(
        default_factory=list, description="Departments allowed by a protected target."
    )

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
    public_scope: str = Field(description="Publication dimension.")
    visibility: str = Field(description="Target visibility.")
    view_depts: list[ViewDept] = Field(
        default_factory=list, description="Departments allowed by a protected target."
    )
    permission_owner: str | None = Field(
        default=None, description="Legacy permission owner."
    )
    friend_approval: str | None = Field(
        default=None, description="Legacy friend approval policy."
    )


class RiskRecordResponse(BaseModel):
    """Public result: the security report detail remains on the external system."""

    model_config = ConfigDict(extra="ignore")
    risk_check_id: str = Field(description="Risk application identifier.")
    phase: str = Field(description="Current application phase.")
    security_status: str | None = Field(
        default=None, description="Latest overall security status."
    )
    detail_url: str | None = Field(
        default=None, description="Link to the original security report."
    )
    auto_poll_paused: bool = Field(
        default=False, description="Whether automatic polling has paused."
    )
    auto_poll_until: datetime | None = Field(
        default=None, description="End of the current automatic polling window."
    )
    next_action_at: datetime | None = Field(
        default=None, description="Next scheduled background action."
    )
    target: RiskTarget = Field(description="Requested publication target.")
    error_reason: str | None = Field(
        default=None, description="Safe operational error reason."
    )
    invalidation_reason: str | None = Field(
        default=None, description="Reason the application lost eligibility."
    )
    approval_puid: str | None = Field(
        default=None, description="Existing publication approval identifier."
    )


class RiskRefreshResponse(BaseModel):
    """Result of refreshing the original security report once."""

    outcome: Literal["UPDATED", "NOT_QUERIED"] = Field(
        description="Whether the original report was queried."
    )
    record: RiskRecordResponse = Field(description="Current local application record.")
