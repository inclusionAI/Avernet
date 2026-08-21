"""Request/response schemas for the bcs publish-to-users endpoint."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ViewDept(BaseModel):
    """An open department scoped onto the publish request (BCS friend gating)."""

    deptNo: str = Field(description="The department's canonical code.")
    deptName: str = Field(description="The department's display name.")


class BcsPublicRequest(BaseModel):
    """New-version publish body (backend route POST /openapi/v1/bots/{bot_uuid}/public-bcs)."""

    public_scope: Literal["user", "agent"] = Field(
        description=(
            "Publish scope. user opens the bot into the user-visible namespace; "
            "agent opens it into the agent/group namespace. Drives the stored "
            "approval sub-block key and the BCS visibility field."
        )
    )
    view_depts: Optional[List[ViewDept]] = Field(
        default=None,
        description=(
            "Open department list (each has deptNo and deptName). Stored on the "
            "approval ticket context; the deptName values join into "
            "friend_ext.<public_*_approval>.view_friend_deps."
        ),
    )
    visibility: Optional[Literal["public", "protected", "private"]] = Field(
        default=None,
        description=(
            "Request target visibility (BCS enum). private short-circuits the "
            "approval ticket and PATCHes the BCS visibility field directly; "
            "public/protected go through the approval flow and are stored on the "
            "pending approval block for the callback to apply."
        ),
    )


class BcsPublishResult(BaseModel):
    """The approval-start result returned by public_bcs_bot.

    Mirrors the ApprovalWorkflowPlugin.start_approval return shape; extra fields
    are kept (forward-compat) so the response surface stays stable as the prod
    approval reply adds keys.
    """

    model_config = ConfigDict(extra="allow")

    success: bool = Field(description="Whether submitting the approval ticket (or the private direct BCS PATCH) succeeded.")
    puid: Optional[str] = Field(default=None, description="The approval ticket's global-unique id (None on the private direct-update path).")
    approval_url: Optional[str] = Field(default=None, description="The approval ticket's review URL (None on the private direct-update path).")
    state: Optional[str] = Field(default=None, description="The ticket's state at submit (PROCESSING / COMPLETED).")
    last_operate: Optional[str] = Field(default=None, description="The ticket's last-operate marker on COMPLETED (AGREE / DISAGREE / CANCEL).")
    error_msg: Optional[str] = Field(default=None, description="Error message when success is False.")
