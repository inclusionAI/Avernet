"""Wire schema for one Team Space Skill's editor approval policy."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CreateSkillEditorRequest(BaseModel):
    """Request Manager edit access to a Team Space Skill."""

    reason: str = Field(
        min_length=1,
        max_length=512,
        description="Reason for requesting Skill edit access.",
    )


class SkillEditorRequestCreated(BaseModel):
    """Work Order result for a Skill editor application."""

    work_order_id: int = Field(description="Created Work Order identifier.")
    work_order_no: str = Field(description="Human-readable Work Order number.")
    status: Literal["PENDING", "APPROVED"] = Field(description="Work Order status.")


class SkillEditorApprovalPolicy(BaseModel):
    """Strict Owner-managed policy for one Team Space Skill."""

    model_config = ConfigDict(extra="forbid")

    auto_approve_editor_requests: bool = Field(
        strict=True,
        description="Whether new qualified editor applications are approved automatically.",
    )
