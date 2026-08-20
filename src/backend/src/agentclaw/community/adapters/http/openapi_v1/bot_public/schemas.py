"""Public response models for the Bot catalog."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RuntimeState = Literal["draft", "verify", "online"]
PublicBotType = Literal["personal", "service", "desktop"]


class PublicBot(BaseModel):
    """The allowlisted public projection of a catalog Bot."""

    bot_id: str = Field(description="Stable public Bot identifier.")
    entity_id: str = Field(description="Public entity identifier for the Bot owner.")
    bot_type: PublicBotType = Field(description="Published kind of Bot.")
    name: str = Field(description="Public Bot display name.")
    description: str = Field(description="Public Bot description.")
    owner_name: str | None = Field(default=None, description="Optional public owner name.")
    engine: str = Field(description="Engine that runs the Bot.")
    status: str = Field(description="Public Bot lifecycle status.")


class Recommendation(BaseModel):
    """Public recommendation information for a discovered Bot."""

    score: float = Field(description="Recommendation relevance score.")
    reasons: list[str] = Field(
        default_factory=list, description="Public reasons for the recommendation."
    )
    short_profile: str | None = Field(
        default=None, description="Optional short public recommendation profile."
    )


class DiscoveredPublicBot(PublicBot):
    """A public catalog Bot enriched with recommendation information."""

    recommendation: Recommendation = Field(description="Public recommendation details.")
