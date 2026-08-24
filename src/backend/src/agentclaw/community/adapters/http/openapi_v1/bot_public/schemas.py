"""Public response models for the Bot catalog."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

RuntimeState = Literal["draft", "verify", "online"]
class PublicBot(BaseModel):
    """The allowlisted public projection of a catalog Bot."""

    bot_id: str = Field(description="Stable public Bot identifier.")
    bot_uuid: str | None = Field(
        default=None,
        description="Canonical BCS Bot UUID returned by Catalog Search when available.",
    )
    entity_id: str = Field(description="Public entity identifier for the Bot owner.")
    bot_type: Any = Field(description="Published kind of Bot.")
    name: str = Field(description="Public Bot display name.")
    description: str = Field(description="Public Bot description.")
    owner_name: Any = Field(default=None, description="Optional public owner name.")
    is_friend: bool | None = Field(
        default=None,
        description="Caller-relative friendship state returned by BCS when available.",
    )
    visibility: Any = Field(
        default=None, description="BCS visibility returned by Catalog Search when available."
    )
    is_online: Any = Field(
        default=None, description="BCS online state returned by Catalog Search when available."
    )
    actor_kind: str | None = Field(
        default=None, description="BCS actor kind returned by Catalog Search when available."
    )
    friend_ext: Any = Field(
        default=None, description="BCS friend extension returned by Catalog Search when available."
    )
    friend_check_in_strategy: Any = Field(
        default=None,
        description="BCS friend check-in strategy returned by Catalog Search when available.",
    )
    user_visibility: Any = Field(
        default=None, description="BCS user visibility returned by Catalog Search when available."
    )
    engine: str = Field(description="Engine that runs the Bot.")
    status: str = Field(description="Public Bot lifecycle status.")


class Recommendation(BaseModel):
    """Public recommendation information for a discovered Bot."""

    score: float = Field(description="Recommendation relevance score.")
    reasons: Any = Field(
        default_factory=list, description="Public reasons for the recommendation."
    )
    short_profile: Any = Field(
        default=None, description="Optional short public recommendation profile."
    )


class DiscoveredPublicBot(PublicBot):
    """A public catalog Bot enriched with recommendation information."""

    recommendation: Recommendation = Field(description="Public recommendation details.")
