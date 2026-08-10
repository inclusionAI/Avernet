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
#
# The example is not decoration either: it is what an API console renders into
# the request pane, and a body model without one arrives at the caller as an
# empty editor. The two belong together on every request model, which is why
# they are produced by one helper rather than declared separately.
def _request_body(example: dict[str, object]) -> ConfigDict:
    """Config for a request-body model: strict, and carrying its example."""
    return ConfigDict(extra="forbid", json_schema_extra={"example": example})

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


_ENGINE_DESC = (
    "Runtime engine that powers the bot. The accepted names are deployment "
    "configuration rather than a fixed set, so an unknown name is refused (400) "
    "instead of being published as an enum here."
)

# Two descriptions, not one, because the request and the response genuinely
# differ. The request side is Literal-constrained, so "no other value" is a
# promise the type keeps. The response side is a plain str copied straight out
# of the record, and desktop bots — which this surface refuses to create or
# manage but still reads — carry a third value. One shared description made the
# response claim an exhaustiveness only the request has.
_BOT_TYPE_REQUEST_DESC = (
    "'personal' for a bot only its owner operates, 'service' for one that can be "
    "published to verify/online runtimes. No other value is accepted."
)

_BOT_TYPE_RESPONSE_DESC = (
    "'personal' for a bot only its owner operates, 'service' for one that can be "
    "published to verify/online runtimes. Other values — 'desktop' in particular "
    "— belong to bots created through a different flow, which this API can read "
    "but not create or manage. Empty when the record carries no type."
)


class Bot(BaseModel):
    """An agent (bot) record."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "bot_id": "20260810_q5o4c89g",
                "bot_name": "Quarterly reporter",
                "bot_desc": "Drafts the quarterly report from the team's notes.",
                "engine": "openclaw",
                "cluster_name": "ACRA",
                "bot_type": "personal",
                "status": "ACTIVE",
                "owner_entity_id": "511488",
            }
        }
    )

    bot_id: str = Field(
        description="Identifier of the bot. Use this value in the path of the "
        "bot-scoped endpoints."
    )
    bot_name: str = Field(description="Human-readable bot name.")
    bot_desc: str = Field(description="What the bot is for; may be empty.")
    engine: str = Field(description=_ENGINE_DESC)
    cluster_name: ClusterName = Field(description=_CLUSTER_DESC)
    bot_type: str = Field(description=_BOT_TYPE_RESPONSE_DESC)
    status: str = Field(description="Lifecycle status: PENDING | ACTIVE | FAILED.")
    owner_entity_id: str = Field(description="The user this bot belongs to.")


class BotCreate(BaseModel):
    """Create-a-bot request body."""

    model_config = _request_body(
        {
            "bot_name": "Quarterly reporter",
            "bot_desc": "Drafts the quarterly report from the team's notes.",
            "engine": "openclaw",
            "cluster_name": "ACRA",
            "bot_type": "personal",
        }
    )

    bot_name: str = Field(
        description="Name for the new bot. Must be unique within your tenant — "
        "check it first with the name-availability endpoint."
    )
    bot_desc: str = Field(description="What the bot is for. Send an empty string "
        "to leave it blank.")
    engine: str = Field(description=_ENGINE_DESC)
    cluster_name: ClusterName = Field(description=_CLUSTER_DESC)
    bot_type: BotType = Field(description=_BOT_TYPE_REQUEST_DESC)
    # ``engine_options`` is deliberately absent. Nothing downstream consumes
    # ``BotCreateSpec.extra_properties`` yet, so declaring the field would
    # publish a contract slot the server rejects on every non-empty value —
    # generated clients would compile a request that always fails. It returns
    # here, unchanged in shape, once ``create_bot`` reads the bag; until then
    # ``extra="forbid"`` names it in the error rather than the schema promising
    # something untrue.


class BotUpdate(BaseModel):
    """Partial update. Omit a field to leave it unchanged.

    Only the name and description can be changed. A bot's engine is fixed at
    creation, its cluster is derived from the engine, and engine options are
    managed through the engine-config endpoints — sending any of those here is
    refused (422) rather than silently ignored.
    """

    # Rejecting them is the point, and the alternative was worse: declaring the
    # immutable fields for symmetry would mean answering 200 to a request that
    # changed nothing, which the caller could only detect by re-reading the bot.

    model_config = _request_body({"bot_name": "Quarterly reporter"})

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
    """Returned (202) when bot creation needs the user to authorize it first.

    Open either handle to complete authorization, then poll the auth-status
    endpoint. Both are surfaced because the issuer supplies one or the other;
    each is empty when it was not supplied.
    """

    bot_id: str = Field(description="Identifier the bot will be created with.")
    iframe_url: str = Field(
        default="", description="Authorization page to embed; empty if none."
    )
    redirect_url: str = Field(
        default="", description="Authorization page to redirect to; empty if none."
    )


class BotAuthStatus(BaseModel):
    """Authorization status of a pending bot creation."""

    status: str = Field(
        description="Authorization state reported by the issuer. The bot is "
        "created once it reads ISSUED."
    )
    message: str | None = Field(
        default=None, description="Why authorization is not complete; null when "
        "there is nothing to report."
    )
    bot: Bot | None = Field(
        default=None, description="The created bot, present only once the "
        "authorization has been issued."
    )


class BotStatus(BaseModel):
    """Runtime readiness of a bot."""

    status: str = Field(description="Lifecycle status: PENDING | ACTIVE | FAILED.")
    is_ready: bool = Field(
        description="True when the bot has a bound device and can take work."
    )
    device_id: str | None = Field(
        default=None, description="Device currently bound to the bot; null when "
        "none is bound yet."
    )


class Ceiling(BaseModel):
    """How many bots the caller may create."""

    ceiling: int = Field(
        description="Maximum number of bots this caller may own. Creating beyond "
        "it is refused."
    )


class Passport(BaseModel):
    """A bot's identity credential."""

    bot_id: str = Field(description="Bot the credential belongs to.")
    passport_id: str = Field(description="Identifier of the issued credential.")
