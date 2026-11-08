"""Display-safe catalog metadata and exact batch query models."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

BotMetadataId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]


class BotMetadataQuery(BaseModel):
    """One exact Bot identity whose display metadata should be resolved."""

    model_config = ConfigDict(extra="forbid")

    bot_id: BotMetadataId = Field(description="Bot identifier.")
    owner_id: BotMetadataId = Field(
        description="Owner identifier paired with bot_id to identify one Bot."
    )


class BotMetadataQueries(BaseModel):
    """Exact Bot identities whose display metadata should be resolved."""

    model_config = ConfigDict(extra="forbid")

    bots: list[BotMetadataQuery] = Field(
        min_length=1,
        max_length=100,
        description=(
            "Bot and owner identifier pairs to resolve. Pairs may come from any "
            "upstream source; duplicates are ignored. At most 100 may be submitted."
        ),
    )


class BotMetadata(BaseModel):
    """Display-safe Bot metadata, without ownership or runtime internals."""

    bot_id: str = Field(description="Unique Bot identifier.")
    owner_id: str = Field(description="Owner identifier paired with the Bot identifier.")
    bot_name: str = Field(description="Display name.")
    bot_desc: str = Field(description="Display description; may be empty.")
    engine: str = Field(description="Engine that powers the Bot.")
    bot_type: str = Field(description="Bot kind, such as personal or service.")
    status: str = Field(description="Current lifecycle status.")
    online_available: bool | None = Field(
        default=None,
        description=(
            "Service Bot has a successful publication with an online binding in "
            "this environment. Not a permission or health check; null for non-service Bots."
        ),
    )
    live_version: int | None = Field(
        default=None, description="Version of that online publication, if available."
    )
    conversation_stage: str = Field(
        default="draft",
        description=(
            "Recommended chat stage: online for service Bots, draft otherwise; "
            "availability and authorization must still be checked."
        ),
    )
