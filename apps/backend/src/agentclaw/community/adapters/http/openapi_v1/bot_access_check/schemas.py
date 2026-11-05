"""Response shapes for the transitional Bot access check."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum


class AccessLevel(_DocumentedEnum):
    """The checked user's level on the Bot."""

    NONE = "none"
    MEMBER = "member"
    ADMIN = "admin"
    OWNER = "owner"

    __descriptions__ = {
        "none": "No access, or no Bot at the given address.",
        "member": "May use the Bot (explicit member editor or Space member).",
        "admin": "May also manage the Bot's editors.",
        "owner": "Owns the Bot.",
    }


class RequiredLevel(_DocumentedEnum):
    """The bar ``has_access`` is measured against."""

    MEMBER = "member"
    ADMIN = "admin"
    OWNER = "owner"

    __descriptions__ = {
        "member": "At least member. The default.",
        "admin": "At least admin.",
        "owner": "Owner only.",
    }


class BotAccess(BaseModel):
    """Whether one user reaches one Bot, and at what level."""

    bot_id: str = Field(description="The addressed Bot id.")
    entity_id: str | None = Field(
        description="The addressed entity id; null when addressed by bot_id alone."
    )
    user_id: str = Field(description="The user whose access was checked.")
    has_access: bool = Field(description="Whether level meets required_level.")
    level: AccessLevel = Field(description="The user's level on the Bot.")
    required_level: RequiredLevel = Field(description="The bar that was applied.")
