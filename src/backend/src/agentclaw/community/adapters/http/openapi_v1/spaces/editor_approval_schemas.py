"""Wire schemas for Team Space Skill editor approval policy."""

from pydantic import BaseModel, Field


class SkillEditorApprovalPolicy(BaseModel):
    """Owner-managed approval policy for one Team Space Skill."""

    auto_approve_editor_requests: bool = Field(
        description=(
            "Whether new qualified editor applications use automatic approval."
        )
    )


class UpdateSkillEditorApprovalPolicy(BaseModel):
    """Replace the approval policy for one Team Space Skill."""

    auto_approve_editor_requests: bool


__all__ = ["SkillEditorApprovalPolicy", "UpdateSkillEditorApprovalPolicy"]
