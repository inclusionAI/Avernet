"""Request/response models for the Bot inventory group."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DeployMode = Literal["cloud", "local"]
BotInventoryKind = Literal["personal_cloud", "local", "service"]
DisplayState = Literal[
    "running",
    "pending",
    "failed",
    "dormant",
    "local_running",
    "local_offline",
    "local_pending",
    "local_failed",
    "service_draft",
    "service_staging",
    "service_online",
    "service_offline",
]
BotAction = Literal[
    "view",
    "chat",
    "edit",
    "delete",
    "restart",
    "data_init",
    "activate",
    "open_folder",
    "passport",
    "engine_config",
    "runtime_logs",
    "engine_restart",
]


class BusinessSpace(BaseModel):
    """Business-space reference consumed from the owning space module."""

    space_id: str
    name: str
    kind: str = Field(description="Space kind from the business-space owner.")


class BotInventoryItem(BaseModel):
    """Unified card for a personal cloud, local, or service Bot."""

    bot_id: str
    bot_name: str
    bot_desc: str
    engine: str
    bot_type: str
    kind: BotInventoryKind
    deploy_mode: DeployMode
    display_state: DisplayState
    status: str
    owner_entity_id: str
    space: BusinessSpace | None = None
    avatar_url: str | None = None
    machine_id: str | None = None
    mount_path: str | None = None
    passport_id: str | None = None
    actions: list[BotAction] = Field(default_factory=list)
    disabled_actions: dict[str, str] | None = None


class BotActions(BaseModel):
    """Action affordances for a Bot inventory item."""

    bot_id: str
    display_state: DisplayState
    actions: list[BotAction]
    disabled_actions: dict[str, str] | None = None
