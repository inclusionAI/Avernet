"""Public response models for Bot inventory cards."""

from typing import Literal

from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.openapi_v1.service_publications.schemas import (
    EditLock,
)
from agentclaw.community.core.bot_copy.types import BotCopyStateSummary


class InventoryPublication(BaseModel):
    """Stored publication identity and status represented by a service Bot card."""

    id: int = Field(description="Publication id represented by this service card.")
    status: str = Field(
        description="Stored publication status, as in the legacy publish.status; "
        "not the Bot status or a normalized display state."
    )
    version: int | None = Field(description="Version of this publication, when assigned.")


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
    "service_deploying",
    "service_prestable",
    "service_staging",
    "service_online",
    "service_offline",
]
BotAction = Literal[
    "view",
    "view_config",
    "chat",
    "edit",
    "delete",
    "restart",
    "restart_publish",
    "data_init",
    "activate",
    "open_folder",
    "passport",
    "engine_config",
    "runtime_logs",
    "engine_restart",
    "publish_staging",
    "publish_online",
    "cancel_staging",
    "upgrade",
    "offline",
    "retry",
]


class BusinessSpace(BaseModel):
    """Business-space reference surfaced on an inventory card."""

    space_id: str = Field(description="Unique identifier of the business space.")
    name: str = Field(description="Display name of the business space.")
    kind: str = Field(description="Space kind from the business-space owner.")


class BotInventoryItem(BaseModel):
    """Unified card for a personal cloud, local, or service bot."""

    expert_name: str | None = Field(
        default=None,
        description="Read-only source expert name for a bot created from the expert market; null for ordinary bots.",
        json_schema_extra={"readOnly": True},
    )
    bot_id: str = Field(description="Unique identifier of the bot.")
    card_id: str = Field(
        description="Stable card identity; service cards include the publication id."
    )
    bot_name: str = Field(description="Display name of the bot.")
    bot_desc: str = Field(
        description="Description of what the bot is for; may be empty."
    )
    engine: str = Field(description="Engine currently assigned to the bot.")
    bot_type: str = Field(
        description="Underlying bot type reported by the bot service."
    )
    kind: BotInventoryKind = Field(
        description="Inventory category used to render the bot card."
    )
    deploy_mode: DeployMode = Field(
        description="Whether the bot runs in the cloud or on a local device."
    )
    display_state: DisplayState = Field(
        description="Normalized lifecycle state used by the inventory view."
    )
    status: str = Field(
        description="Stored Bot status for every card kind, identical in meaning to /bots status. For service publication state use publish.status or display_state."
    )
    copy_state: BotCopyStateSummary | None = Field(
        default=None,
        description="Derive-on-read copy operation state for this bot; null when the bot is not a copy dst or the copy already converged. FE uses it for the copy-progress badge and retry action on inventory cards.",
    )
    publish: InventoryPublication | None = Field(
        default=None,
        description="Publication represented by this service card; null for non-service Bots or when no publication exists.",
    )
    internal_status: str | None = Field(
        default=None,
        description="Stored service-publication status for diagnostics; null for non-service cards.",
    )
    publication_id: int | None = Field(
        default=None,
        description="Service publication id represented by this card; otherwise null.",
    )
    publication_version: int | None = Field(
        default=None,
        description="Service publication version represented by this card; otherwise null.",
    )
    live_version: int | None = Field(
        default=None, description="Currently running service version, when one exists."
    )
    owner_entity_id: str = Field(description="User who owns the bot.")
    space: BusinessSpace | None = Field(
        default=None, description="Business space containing the bot, when resolved."
    )
    template_type: str | None = Field(
        default=None,
        description="Template type of the bot, e.g. 'applicationCoding'; null for "
        "bots created without a template.",
    )
    template_config: dict | None = Field(
        default=None,
        description="Stored template snapshot, copied verbatim from the bot's "
        "creation input (no field filtering). Null without a template. May "
        "carry sensitive values the creator supplied (e.g. 'token', "
        "'bot_template_config.ext_config.thetaKey') and the server-managed "
        "'engine_form' marker — treat as sensitive.",
    )
    avatar_url: str | None = Field(
        default=None, description="Avatar URL for the bot, when configured."
    )
    machine_id: str | None = Field(
        default=None,
        description="Host device identifier for a local bot; otherwise null.",
    )
    mount_path: str | None = Field(
        default=None,
        description="Mounted workspace path for a local bot; otherwise null.",
    )
    passport_id: str | None = Field(
        default=None,
        description="Platform identity credential identifier, when issued.",
    )
    actions: list[BotAction] = Field(
        default_factory=list, description="Actions currently available for this bot."
    )
    disabled_actions: dict[str, str] | None = Field(
        default=None,
        description="Unavailable actions mapped to caller-facing reasons, when any.",
    )
    edit_lock: EditLock | None = Field(
        default=None,
        description="Current Bot-level collaborative edit lock for an operable service Bot; null for other cards.",
    )
