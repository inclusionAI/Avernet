"""Request/response models for the bots group."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.clusters import ClusterName

# Request bodies reject unknown keys. Pydantic's default is to *ignore* them,
# which on a public API means a typo'd or immutable field (``engine`` on update)
# is silently dropped and the caller gets a 200 believing it was applied. With
# ``forbid`` the request fails validation instead — and the public validation
# handler renders that as the standard Envelope.
_STRICT = ConfigDict(extra="forbid")

# The only two types this creation flow can carry to completion. "desktop" bots
# are inserted and then deliberately skipped by create_bot's device allocation,
# so accepting one would return 201/ISSUED for a permanently PENDING bot; they
# have their own creation flow. Single-sourced here because the restriction has
# to hold on *both* entry points that can insert a bot — the create body and the
# authorization-completion query params — and those must not drift apart.
BotType = Literal["personal", "service"]

_CLUSTER_DESC = (
    "Deployment cluster, in strict 1:1 correspondence with the engine: "
    "'ANDC' for engine 'teclaw', 'ACRA' for every other engine. On create the "
    "engine/cluster pair is validated against this rule (400 on mismatch)."
)


class Bot(BaseModel):
    """An agent (bot) record."""

    bot_id: str
    bot_name: str
    bot_desc: str
    engine: str
    cluster_name: ClusterName = Field(description=_CLUSTER_DESC)
    bot_type: str
    status: str = Field(description="Lifecycle status: PENDING | ACTIVE | FAILED.")
    owner_entity_id: str


class BotCreate(BaseModel):
    """Create-a-bot request body."""

    model_config = _STRICT

    bot_name: str
    bot_desc: str
    engine: str
    cluster_name: ClusterName = Field(description=_CLUSTER_DESC)
    bot_type: BotType
    engine_options: dict[str, Any] = Field(
        default_factory=dict,
        description="Engine/vendor-specific inputs, kept nested rather than "
        "flattened into the request body.",
    )


class BotUpdate(BaseModel):
    """Partial update; engine is fixed at creation and cannot change.

    ``engine`` is deliberately absent *and* rejected: with unknown keys forbidden
    a caller that sends one gets a validation error rather than a 200 that
    silently ignored their requested engine change.
    """

    model_config = _STRICT

    bot_name: str | None = None
    bot_desc: str | None = None
    cluster_name: str | None = None
    engine_options: dict[str, Any] | None = None


class BotAuthPending(BaseModel):
    """Returned (202) when bot creation needs user authorization (Passport).

    Passport may hand back either handle, so both are surfaced: a caller that
    only receives ``redirect_url`` would otherwise have no way to complete
    authorization. Each is empty when Passport did not supply it.
    """

    bot_id: str
    iframe_url: str = ""
    redirect_url: str = ""


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
