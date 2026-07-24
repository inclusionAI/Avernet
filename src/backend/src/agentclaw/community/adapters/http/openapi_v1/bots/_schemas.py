"""Request/response models for the bots group."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Bot(BaseModel):
    """An agent (bot) record."""

    bot_id: str
    bot_name: str
    bot_desc: str
    engine: str
    cluster_name: str
    bot_type: str
    status: str = Field(description="Lifecycle status: PENDING | ACTIVE | FAILED.")
    owner_entity_id: str


class BotCreate(BaseModel):
    """Create-a-bot request body."""

    bot_name: str
    bot_desc: str
    engine: str
    cluster_name: str
    bot_type: str
    engine_options: dict[str, Any] = Field(
        default_factory=dict,
        description="Engine/vendor-specific inputs, kept nested rather than "
        "flattened into the request body.",
    )


class BotUpdate(BaseModel):
    """Partial update; engine is fixed at creation and cannot change."""

    bot_name: str | None = None
    bot_desc: str | None = None
    cluster_name: str | None = None
    engine_options: dict[str, Any] | None = None


class BotAuthPending(BaseModel):
    """Returned (202) when bot creation needs user authorization (Passport)."""

    bot_id: str
    iframe_url: str


class BotAuthStatus(BaseModel):
    """Passport authorization status; ``bot`` is present once ISSUED."""

    status: str
    message: str | None = None
    bot: Bot | None = None


class BotStatus(BaseModel):
    """Runtime / device readiness of a bot."""

    status: str
    is_ready: bool
    device_id: str | None = None


class Ceiling(BaseModel):
    """Per-caller bot creation quota ceiling."""

    ceiling: int


class Passport(BaseModel):
    """Agent Passport (identity credential) summary."""

    bot_id: str
    passport_id: str
