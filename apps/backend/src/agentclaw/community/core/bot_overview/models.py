"""Engine-neutral Bot overview models.

The overview is served **without authentication**, so every field here must be
non-user data about the Bot: no owner identity, no conversation content, no
credentials. Session information is deliberately reduced to activity
aggregates so a client can apply its own policy (for example "was there any
conversation in the last two weeks") without the API hard-coding one.

``None`` on a neutral field means "the Bot's engine did not report it" — an
engine without an overview provider, or one whose provider does not fill that
field yet. An empty list means the engine reported that there are none.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from .payloads import EngineSpecificPayload


class SkillSummary(BaseModel):
    """One Skill active on the Bot."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Skill name as the engine exposes it.")
    version: str | None = Field(
        default=None,
        description="Active Skill version, or null when the engine does not "
        "version this Skill.",
    )


class McpServerSummary(BaseModel):
    """One MCP server configured on the Bot."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="MCP server name as the engine exposes it.")
    transport: str | None = Field(
        default=None,
        description="Transport such as 'stdio', 'sse' or 'streamable-http'; "
        "null when the engine does not report it.",
    )


class DailySessionActivity(BaseModel):
    """Session activity aggregated over one UTC calendar day."""

    model_config = ConfigDict(extra="forbid")

    day: date = Field(description="UTC calendar day the counts cover.")
    session_count: int = Field(
        ge=0, description="Distinct sessions with at least one message that day."
    )
    message_count: int = Field(ge=0, description="Messages exchanged that day.")


class SessionActivity(BaseModel):
    """Generic session activity, for clients to apply their own policy."""

    model_config = ConfigDict(extra="forbid")

    window_start: datetime = Field(
        description="Inclusive UTC start of the window that ``daily`` covers."
    )
    last_activity_at: datetime | None = Field(
        default=None,
        description="Most recent message in any session, regardless of the "
        "window; null when the Bot has never had a conversation.",
    )
    daily: list[DailySessionActivity] = Field(
        default_factory=list,
        description="One entry per UTC day inside the window that had "
        "activity, oldest first. Days without activity are omitted.",
    )


class EngineBotOverview(BaseModel):
    """What an engine provider contributes to a Bot overview.

    Every field defaults to "not reported", so a provider fills only what its
    engine can answer.
    """

    model_config = ConfigDict(extra="forbid")

    default_model: str | None = None
    skills: list[SkillSummary] | None = None
    mcps: list[McpServerSummary] | None = None
    sessions: SessionActivity | None = None
    payload: EngineSpecificPayload | None = None


class BotOverview(BaseModel):
    """Non-user overview of one Bot: engine-neutral fields plus engine payload."""

    model_config = ConfigDict(extra="forbid")

    bot_id: str = Field(description="The Bot's identifier.")
    engine: str = Field(
        description="The Bot's persisted engine, e.g. 'openclaw', "
        "'claude_code', 'aicoding'."
    )
    status: str = Field(description="The Bot's lifecycle status, e.g. 'ACTIVE'.")
    observed_at: datetime = Field(
        description="UTC time this overview was assembled. Compare activity "
        "timestamps against it rather than the client clock."
    )
    default_model: str | None = Field(
        default=None,
        description="Model the Bot uses when none is chosen per request; null "
        "when the engine did not report it.",
    )
    skills: list[SkillSummary] | None = Field(
        default=None,
        description="Skills active on the Bot; null when the engine did not "
        "report them.",
    )
    mcps: list[McpServerSummary] | None = Field(
        default=None,
        description="MCP servers configured on the Bot; null when the engine "
        "did not report them.",
    )
    sessions: SessionActivity | None = Field(
        default=None,
        description="Session activity aggregates; null when the engine did "
        "not report them.",
    )
    payload: EngineSpecificPayload | None = Field(
        default=None,
        description="Engine-specific fields, discriminated by ``engine``; null "
        "when the Bot's engine has no overview provider.",
    )


__all__ = [
    "BotOverview",
    "DailySessionActivity",
    "EngineBotOverview",
    "McpServerSummary",
    "SessionActivity",
    "SkillSummary",
]
