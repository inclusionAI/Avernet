"""Public schemas for service-Bot publication lifecycle operations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


_STRICT = ConfigDict(extra="forbid")


class ServiceDeployment(BaseModel):
    """Current or most recent failed deployment for one publication."""

    action: Literal["publish_staging", "publish_online", "restart_publish"] = Field(
        description="Lifecycle action being executed or most recently failed."
    )
    target: Literal["prestable", "staging", "running"] = Field(
        description="Product state the deployment is trying to reach. "
        "The legacy staging value remains schema-compatible but is no longer emitted."
    )
    status: Literal["running", "failed"] = Field(
        description="Current deployment execution state."
    )
    error_message: str | None = Field(
        default=None, description="Sanitized failure guidance; null while running."
    )
    started_at: datetime | None = Field(
        default=None, description="Best-known deployment start timestamp."
    )
    finished_at: datetime | None = Field(
        default=None, description="Failure completion timestamp, when applicable."
    )


class ServiceApproval(BaseModel):
    """Approval state attached to an online/offline operation."""

    required: bool = Field(description="Whether this operation requires approval.")
    status: str | None = Field(
        default=None, description="Current approval workflow status."
    )
    approval_id: str | None = Field(
        default=None, description="External approval workflow identifier."
    )
    approval_url: str | None = Field(
        default=None, description="Approval page URL, when one is available."
    )


class ServicePublication(BaseModel):
    """One independently actionable service-Bot version card."""

    bot_id: str = Field(description="Stable Bot identifier.")
    publication_id: int = Field(description="Publication version identifier.")
    card_id: str = Field(
        description="Stable UI identity composed from bot_id and publication_id."
    )
    version: int = Field(description="Monotonic service-Bot publication version.")
    status: Literal[
        "draft", "deploying", "prestable", "staging", "running", "offline"
    ] = Field(
        description="Stable product lifecycle state for this version card. "
        "The legacy staging value remains schema-compatible but is no longer emitted."
    )
    internal_status: str = Field(
        description="Stored publication state; use status for stable UI behavior."
    )
    live_version: int | None = Field(
        default=None, description="Currently running version for this Bot, if any."
    )
    deployment: ServiceDeployment | None = Field(
        default=None, description="Current or most recent failed deployment."
    )
    approval: ServiceApproval | None = Field(
        default=None, description="Current online/offline approval state."
    )
    available_actions: list[
        Literal[
            "publish_staging",
            "publish_online",
            "restart_publish",
            "cancel_staging",
            "offline",
            "retry",
            "delete",
        ]
    ] = Field(description="Commands currently valid for the caller and state.")
    created_at: datetime = Field(description="Publication creation timestamp.")
    updated_at: datetime = Field(description="Last publication update timestamp.")


class ServicePublicationList(BaseModel):
    """The at-most-two cards exposed for one service Bot."""

    bot_id: str = Field(description="Stable Bot identifier.")
    items: list[ServicePublication] = Field(
        description="At most two visible publication version cards."
    )


class ServicePublicationOperation(BaseModel):
    """Acknowledgement for an asynchronous lifecycle command."""

    bot_id: str = Field(description="Stable Bot identifier.")
    publication_id: int = Field(description="Publication version identifier.")
    action: Literal[
        "publish_staging",
        "publish_online",
        "restart_publish",
        "cancel_staging",
        "offline",
        "retry",
    ] = Field(description="Lifecycle command accepted by the service.")
    accepted: bool = Field(description="Whether the command was accepted.")
    operation_status: Literal["pending", "waiting_approval"] = Field(
        description="Whether execution is queued or blocked on approval."
    )
    approval: ServiceApproval | None = Field(
        default=None, description="Approval state when the command requires it."
    )


class ServiceBotConfig(BaseModel):
    """Owner-managed service-Bot lifecycle configuration."""

    model_config = _STRICT

    bot_id: str = Field(description="Stable Bot identifier.")
    should_approval: bool = Field(
        description="Whether non-owner online/offline commands require approval."
    )


class ServiceBotConfigUpdate(BaseModel):
    """Mutable lifecycle configuration exposed by the public API."""

    model_config = _STRICT

    should_approval: bool = Field(
        description="Whether non-owner online/offline commands require approval."
    )


class LifecycleAdvanceRequest(BaseModel):
    """Target stage for advancing the current service-Bot publication."""

    model_config = _STRICT

    stage: Literal["prestable", "staging", "online"] = Field(
        description="Advance draft to prestable or prestable to online. "
        "The legacy value 'staging' is accepted as an alias for 'prestable'."
    )


class LifecycleRestartRequest(BaseModel):
    """Published runtime selected for restart."""

    model_config = _STRICT

    stage: Literal["prestable", "staging", "online"] = Field(
        description="Restart the prestable or online runtime. "
        "The legacy value 'staging' is accepted as an alias for 'prestable'."
    )


class EditLock(BaseModel):
    """Current collaborative edit-lock state."""

    locked: bool = Field(description="Whether an edit lock currently exists.")
    acquired: bool | None = Field(
        default=None,
        description="Whether this acquire/steal request obtained the lock.",
    )
    holder_user_id: str | None = Field(
        default=None, description="Current lock holder's user identifier."
    )
    holder_name: str | None = Field(
        default=None, description="Current lock holder's display name."
    )
    has_collaborators: bool = Field(
        description="Whether this Bot has collaborators besides its Owner."
    )
    is_owner_holder: bool = Field(
        default=False, description="Whether the current lock holder is the Bot Owner."
    )
    need_lock: bool = Field(
        description="Whether collaborative draft writes require an edit lock."
    )


class EditLockRelease(BaseModel):
    """Result of releasing an edit lock."""

    released: bool = Field(description="Whether the caller's edit lock was released.")
