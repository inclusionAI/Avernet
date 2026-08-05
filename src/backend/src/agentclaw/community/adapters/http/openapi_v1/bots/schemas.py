"""Request/response models for the bots group."""

from __future__ import annotations

from typing import Literal

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
    # ``engine_options`` is deliberately absent. Nothing downstream consumes
    # ``BotCreateSpec.extra_properties`` yet, so declaring the field would
    # publish a contract slot the server rejects on every non-empty value —
    # generated clients would compile a request that always fails. It returns
    # here, unchanged in shape, once ``create_bot`` reads the bag; until then
    # ``extra="forbid"`` names it in the error rather than the schema promising
    # something untrue.


class BotUpdate(BaseModel):
    """Partial update — only the fields this operation can actually apply.

    ``engine``, ``cluster_name`` and ``engine_options`` are all deliberately
    absent *and* rejected by ``extra="forbid"``. Declaring them for symmetry
    would mean answering 200 to a request that changed nothing: the engine is
    fixed at creation, ``cluster_name`` is derived from it, and engine options
    are managed through the engine-config endpoints. A caller that sends one now
    gets a validation error naming the field instead of a success they have to
    verify by re-reading the bot.
    """

    model_config = _STRICT

    # Declared ``str`` with a ``None`` default on purpose. Omitting a field means
    # "leave it alone" — that is what the default encodes, and defaults are not
    # validated — while sending an explicit ``null`` is rejected instead of
    # silently doing nothing: ``BotService.update_bot`` reads ``None`` as
    # "field omitted", so a schema-valid ``{"bot_desc": null}`` would answer 200
    # having changed nothing. The generated schema types both as non-nullable
    # strings, so a client cannot compile the request that gets rejected.
    #
    # This means the surface has no way to *clear* a description back to null.
    # Neither does the internal route — ``update_bot`` has no representation for
    # it — so that is a product gap to close deliberately, not something to fake
    # here by treating "" as null.
    bot_name: str = Field(default=None, description="New name; omit to keep.")
    bot_desc: str = Field(default=None, description="New description; omit to keep.")


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
