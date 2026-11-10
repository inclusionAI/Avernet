"""Engine-neutral Bot overview models.

The overview is served to callers that present **no user credential**, so
every field here must be non-user data about the Bot: no owner identity, no
conversation content, no credentials. Session information is deliberately
reduced to the time of the last session so a client can apply its own policy (for example "was there any
conversation in the last two weeks") without the API hard-coding one.

Skills, MCP servers and session activity are platform-owned: the service fills
them from the Skill Center and the engine-neutral session route for every
engine. Only ``default_model`` and the engine payload come from per-engine
providers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .payloads import EngineSpecificPayload


# Skill Type as CONTEXT.md defines it: how a Skill's content is managed and
# delivered. A Literal (not an Enum) so the published document lists the
# members inline, with their meanings in the field description.
SkillType = Literal["local", "repo", "center"]


class SkillSummary(BaseModel):
    """One Skill in the Bot's effective Skill set."""

    model_config = ConfigDict(extra="forbid")

    skill_type: SkillType = Field(
        description="Skill type. 'local': uploaded directly to this Bot, "
        "referencing no managed Skill. 'repo': from a Skill repository. "
        "'center': governed, versioned Skill Center content."
    )
    skill_id: str = Field(
        description="Stable identifier of the Skill within its type: the "
        "Skill Center Skill identity for 'center', the repository Skill "
        "identifier for 'repo', the Bot-local Skill name for 'local'."
    )
    name: str = Field(description="Skill display name.")
    version: str | None = Field(
        default=None,
        description="Active Skill version, or null when the engine does not "
        "version this Skill.",
    )


class McpServerSummary(BaseModel):
    """One MCP server configured on the Bot."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        description="MCP server code, e.g. 'mcp.example.weather' — the same "
        "identifier the MCP catalogue uses."
    )
    transport: str | None = Field(
        default=None,
        description="Transport such as 'stdio', 'sse' or 'streamable-http'; "
        "null when the engine does not report it.",
    )


class SessionActivity(BaseModel):
    """Generic session activity, for clients to apply their own policy."""

    model_config = ConfigDict(extra="forbid")

    last_session_at: datetime | None = Field(
        default=None,
        description="UTC time of the Bot's most recent session activity; null "
        "when none is known: the Bot has had no session, or its runtime could "
        "not be reached.",
    )


class EngineBotOverview(BaseModel):
    """What an engine provider contributes to a Bot overview.

    Skills, MCP servers and session activity are platform-owned and filled by
    ``BotOverviewService`` for every engine, so a provider cannot report them.
    Every field defaults to "nothing reported".
    """

    model_config = ConfigDict(extra="forbid")

    default_model: str | None = None
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
        description="UTC time this overview was assembled. Compare session "
        "timestamps against it rather than the client clock."
    )
    default_model: str | None = Field(
        default=None,
        description="Model the Bot uses when none is chosen per request; null "
        "when the engine did not report it.",
    )
    skills: list[SkillSummary] = Field(
        default_factory=list,
        description="Skills in the Bot's effective Skill set.",
    )
    mcps: list[McpServerSummary] = Field(
        default_factory=list,
        description="MCP servers configured on the Bot.",
    )
    sessions: SessionActivity = Field(
        default_factory=SessionActivity,
        description="Session activity, e.g. when the Bot last had a session.",
    )
    payload: EngineSpecificPayload | None = Field(
        default=None,
        description="Engine-specific fields, discriminated by engine; null "
        "when the Bot's engine has no overview provider.",
    )


__all__ = [
    "BotOverview",
    "EngineBotOverview",
    "McpServerSummary",
    "SessionActivity",
    "SkillSummary",
    "SkillType",
]
