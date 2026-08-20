"""Public contracts for Bot Channel configuration.

Only DingTalk is a supported Channel provider today. Secrets are accepted on
writes but represented only by ``has_client_secret`` on reads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ChannelType = Literal["dingding"]
ChannelStatus = Literal["active", "inactive"]
ChannelStage = Literal["draft"]


class DingTalkChannelConfigCreate(BaseModel):
    """DingTalk credentials and behavior for a new Channel."""

    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1, description="DingTalk application client id.")
    client_secret: str = Field(
        min_length=1,
        description="DingTalk application secret. Write-only and never returned.",
        json_schema_extra={"writeOnly": True},
    )
    card_template_id: str | None = Field(
        default=None, description="DingTalk interactive-card template identifier."
    )
    card_template_key: str | None = Field(
        default=None, description="Template field used for the card message body."
    )
    enable_streaming_cards: bool = Field(
        default=False, description="Whether DingTalk cards update while output streams."
    )
    dm_policy: Literal["open", "disabled"] = Field(
        default="open", description="Whether direct messages may reach this Channel."
    )
    allowlist: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Sender identifiers allowed to use the Channel; '*' permits all.",
    )
    reply_to_message: bool = Field(
        default=True, description="Whether replies attach to the source message."
    )
    robot_code: str = Field(
        default="", description="Optional DingTalk robot code used by the application."
    )
    aix_enable: bool = Field(
        default=True, description="Whether the DingTalk AI-card extension is enabled."
    )
    include_sender_name: bool = Field(
        default=True, description="Whether the sender's name enters Bot context."
    )


class DingTalkChannelConfigUpdate(BaseModel):
    """Partial DingTalk config update; omitted values remain unchanged."""

    model_config = ConfigDict(extra="forbid")

    client_id: str | None = Field(
        default=None, min_length=1, description="Replacement client id; omit to keep."
    )
    client_secret: str | None = Field(
        default=None,
        min_length=1,
        description="Replacement secret. Omit or send null to preserve the stored secret.",
        json_schema_extra={"writeOnly": True},
    )
    card_template_id: str | None = Field(
        default=None, description="Replacement card-template id; null clears it."
    )
    card_template_key: str | None = Field(
        default=None, description="Replacement card-template key; null clears it."
    )
    enable_streaming_cards: bool | None = Field(
        default=None, description="Enable or disable streaming cards; omit to keep."
    )
    dm_policy: Literal["open", "disabled"] | None = Field(
        default=None, description="New direct-message policy; omit to keep."
    )
    allowlist: list[str] | None = Field(
        default=None, description="Replacement sender allowlist; omit to keep."
    )
    reply_to_message: bool | None = Field(
        default=None, description="New reply-linking behavior; omit to keep."
    )
    robot_code: str | None = Field(
        default=None, description="Replacement DingTalk robot code; omit to keep."
    )
    aix_enable: bool | None = Field(
        default=None, description="Enable or disable the DingTalk AI-card extension."
    )
    include_sender_name: bool | None = Field(
        default=None,
        description="Whether sender names enter Bot context; omit to keep.",
    )


class DingTalkChannelConfig(BaseModel):
    """Safe DingTalk config projection; never contains the client secret."""

    client_id: str = Field(description="DingTalk application client id.")
    has_client_secret: bool = Field(
        description="Whether a secret is stored; its value is never returned."
    )
    card_template_id: str | None = Field(
        default=None, description="DingTalk interactive-card template identifier."
    )
    card_template_key: str | None = Field(
        default=None, description="Template field used for the card message body."
    )
    enable_streaming_cards: bool = Field(
        default=False, description="Whether DingTalk cards update while output streams."
    )
    dm_policy: str = Field(
        default="open", description="Direct-message policy stored for the Channel."
    )
    allowlist: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Sender identifiers allowed to use the Channel; '*' permits all.",
    )
    reply_to_message: bool = Field(
        default=True, description="Whether replies attach to the source message."
    )
    robot_code: str = Field(
        default="", description="Optional DingTalk robot code used by the application."
    )
    aix_enable: bool = Field(
        default=True, description="Whether the DingTalk AI-card extension is enabled."
    )
    include_sender_name: bool = Field(
        default=True, description="Whether the sender's name enters Bot context."
    )


class ChannelCreate(BaseModel):
    """Create one inactive draft Channel on the Bot named in the path."""

    model_config = ConfigDict(extra="forbid")

    type: ChannelType = Field(
        description="Channel provider; only 'dingding' is supported."
    )
    description: str | None = Field(
        default=None, description="Optional human-readable purpose of the Channel."
    )
    config: DingTalkChannelConfigCreate = Field(
        description="DingTalk credentials and message behavior."
    )


class ChannelUpdate(BaseModel):
    """Partial update of an existing draft Channel."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(
        default=None, description="New description; null clears it, omit to keep."
    )
    config: DingTalkChannelConfigUpdate | None = Field(
        default=None,
        description="Partial DingTalk config update; omit to keep all config.",
    )


class ChannelStatusUpdate(BaseModel):
    """Activate or deactivate a Channel."""

    model_config = ConfigDict(extra="forbid")

    status: ChannelStatus = Field(
        description="Target runtime state: 'active' or 'inactive'."
    )


class Channel(BaseModel):
    """One Bot-bound external Channel."""

    id: int = Field(description="Numeric Channel identifier assigned by the Backend.")
    type: ChannelType = Field(
        description="Channel provider; currently always 'dingding'."
    )
    description: str | None = Field(description="Human-readable purpose, if set.")
    bot_id: str = Field(description="Bot this Channel is bound to.")
    owner_id: str = Field(description="Owner of the Bot this Channel is bound to.")
    config: DingTalkChannelConfig = Field(
        description="Safe DingTalk config projection with no credential value."
    )
    status: ChannelStatus = Field(
        description="Current public state: active or inactive."
    )
    stage: ChannelStage = Field(
        default="draft",
        description="Configuration stage; the public API exposes draft only.",
    )
    created_at: datetime | None = Field(
        default=None, description="Channel creation time when available."
    )
    updated_at: datetime | None = Field(
        default=None, description="Most recent persisted update time when available."
    )
