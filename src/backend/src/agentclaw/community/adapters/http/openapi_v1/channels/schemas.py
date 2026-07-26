"""Request/response models for the channels group (DingTalk only in v1)."""

from __future__ import annotations

from pydantic import BaseModel


class ChannelConfigWrite(BaseModel):
    """DingTalk config on write; ``client_secret`` is write-only."""

    client_id: str
    client_secret: str | None = None
    card_template_id: str | None = None
    dm_policy: str | None = None


class ChannelConfig(BaseModel):
    """DingTalk config as returned (never includes ``client_secret``)."""

    client_id: str
    card_template_id: str | None = None
    dm_policy: str | None = None


class Channel(BaseModel):
    """A channel binding a bot to an external channel."""

    id: str
    type: str  # "dingding" in v1
    description: str
    bot_id: str
    config: ChannelConfig
    status: str  # active | inactive
    gmt_create: str
    gmt_modified: str


class ChannelCreate(BaseModel):
    """Create-a-channel request body (status starts inactive)."""

    type: str  # only "dingding" in v1
    description: str
    bot_id: str
    config: ChannelConfigWrite


class ChannelUpdate(BaseModel):
    """Full update of a channel; stored client_secret is kept if omitted."""

    description: str
    config: ChannelConfigWrite
    status: str | None = None  # active | inactive


class ChannelStatusUpdate(BaseModel):
    """Toggle a channel active/inactive."""

    status: str  # active | inactive
