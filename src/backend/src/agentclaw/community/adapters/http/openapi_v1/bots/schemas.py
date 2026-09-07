"""Request/response models for the bots group.

Docstrings and field descriptions here are published verbatim into the OpenAPI
document external tenants read — keep them caller-facing prose. Rationale and
internal names belong in ``#`` comments.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from agentclaw.community.adapters.http.openapi_v1.clusters import ClusterName
from agentclaw.community.adapters.http.openapi_v1.service_publications.schemas import (
    EditLock,
)

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

_BOT_TYPE_DESC = (
    "Kind of bot to create: 'personal' — a bot for the named user's own use, "
    "with a single draft runtime; 'service' — a bot built to be published, "
    "gaining verify/online runtimes through its publish lifecycle."
)

_CLUSTER_DESC = (
    "Deployment cluster, in strict 1:1 correspondence with the engine: "
    "'ANDC' for engine 'teclaw', 'ACRA' for every other engine. On create the "
    "engine/cluster pair is validated against this rule (400 on mismatch)."
)

_ENGINE_DESC = (
    "Engine that powers the bot, fixed at creation. The valid set is "
    "deployment-configured — read it from the engine group's available-engines "
    "endpoint; an unlisted engine is refused (400). Internal implementation "
    "engines (e.g. 'aicoding', the internal runtime behind 'claude_code') are "
    "not accepted; runtime forms travel on the template, not the engine."
)

# The status vocabulary is a pass-through of the stored lifecycle state, and the
# listing can also surface bots whose lifecycle is driven elsewhere (desktop
# bots, dormancy reclaim), so the set is open: every value those lifecycles
# emit today is listed with its meaning, but it is not published as a schema
# `enum` — a closed set would make a legitimately-listed bot invalid the day a
# lifecycle adds a state.
_BOT_STATUS_DESC = (
    "Lifecycle status. Values: 'PENDING' — created, its device is still "
    "being provisioned; 'ACTIVE' — running and reachable; 'FAILED' — device "
    "provisioning failed (restart, or delete and recreate); 'OFFLINE', "
    "'RELEASING', 'RELEASED' — desktop-bot lifecycle states; 'RECYCLED', "
    "'REACTIVATING' — dormant-bot reclaim states. New lifecycles can add "
    "values, so treat any value other than 'ACTIVE' as not ready for work."
)


class Bot(BaseModel):
    """An agent (bot) record."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "bot_id": "20260813_a7k2m9p1",
                "bot_name": "research-assistant",
                "bot_desc": "Summarizes weekly industry news.",
                "engine": "openclaw",
                "cluster_name": "ACRA",
                "bot_type": "personal",
                "status": "ACTIVE",
                "owner_entity_id": "u_165137",
                "template_type": None,
                "template_config": None,
                "space": None,
            }
        }
    )

    bot_id: str = Field(
        description="Unique identifier of the bot, e.g. '20260813_a7k2m9p1'. "
        "Use it verbatim wherever an operation takes a bot_id."
    )
    bot_name: str = Field(description="Display name; unique within the tenant.")
    bot_desc: str = Field(
        description="Free-form description of what the bot is for; may be empty."
    )
    engine: str = Field(description=_ENGINE_DESC)
    cluster_name: ClusterName = Field(description=_CLUSTER_DESC)
    # Typed `str`, not BotType: the listing also returns bots this surface does
    # not create (desktop), so a closed request-side set would fail on read.
    bot_type: str = Field(
        description="Kind of bot: 'personal', 'service', or 'desktop'. Desktop "
        "bots run on the user's own machine; they appear in listings but their "
        "lifecycle is not managed through this API."
    )
    status: str = Field(description=_BOT_STATUS_DESC)
    owner_entity_id: str = Field(
        description="The user who owns the bot — echoes the user_id the request named."
    )
    template_type: str | None = Field(
        default=None,
        description="Template type of the bot, e.g. 'applicationCoding'; null for "
        "bots created without a template.",
    )
    template_config: dict | None = Field(
        default=None,
        description="Stored template snapshot, copied verbatim from the bot's "
        "creation input (no field filtering). Null without a template. May "
        "carry sensitive values the creator supplied (e.g. 'token', "
        "'bot_template_config.ext_config.thetaKey') and the server-managed "
        "'engine_form' marker — treat as sensitive.",
    )
    space: BusinessSpace | None = Field(
        default=None,
        description="Business space the bot's record is assigned to (owner view). "
        "Populated on the listing endpoints.",
    )


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


class BotCreateEngineProperties(BaseModel):
    """Engine-specific properties used while creating a bot."""

    model_config = ConfigDict(extra="forbid")

    template_type: str | None = Field(
        description=(
            "Template type declared with the config. Required for template-"
            "factory snapshots (any value, echoed from available-tc-list); "
            "for hand-written application-coding configs omit it or pass "
            "'applicationCoding'."
        ),
        default=None,
    )
    template_config: dict[str, Any] = Field(
        description=(
            "Template configuration. Either hand-written application-coding "
            "properties, or a template-factory snapshot (identified by "
            "template_key + template_uid) echoed verbatim from "
            "bot-templates/available-tc-list; platform-managed identity and "
            "lifecycle fields are not accepted."
        ),
    )


class BotCreate(BaseModel):
    """Create-a-bot request body."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "bot_name": "research-assistant",
                "bot_desc": "Summarizes weekly industry news.",
                "engine": "openclaw",
                "cluster_name": "ACRA",
                "bot_type": "personal",
            }
        },
    )

    bot_name: str = Field(
        description="Display name. Must be non-blank, must not contain '@', "
        "and must be unused within the tenant (409 on a duplicate); leading "
        "and trailing whitespace is trimmed."
    )
    bot_desc: str = Field(description="Description of what the bot is for.")
    engine: str = Field(description=_ENGINE_DESC)
    cluster_name: ClusterName = Field(description=_CLUSTER_DESC)
    bot_type: BotType = Field(description=_BOT_TYPE_DESC)
    space_id: str | None = Field(
        default=None,
        description="Business space to associate with the bot, when applicable.",
    )
    engine_properties: BotCreateEngineProperties | None = Field(
        default=None,
        description=(
            "Optional engine-specific properties. Omit for a plain bot; provide "
            "template_config (hand-written application-coding or a "
            "template-factory snapshot) for a template-backed bot."
        ),
    )
    # ``engine_options`` is deliberately absent. The engine-owned bag the
    # server actually consumes is ``engine_properties`` above (routed to the
    # engine-selected ``EngineProvisioningStrategy.prepare_create``); an
    # additional options slot would publish a contract nothing reads, so
    # every non-empty value would be a request the server always rejects —
    # generated clients would compile a request that always fails. It returns
    # here, unchanged in shape, once ``create_bot`` reads such a bag; until
    # then ``extra="forbid"`` names it in the error rather than the schema
    # promising something untrue.


class BotSpaceUpdate(BaseModel):
    """Change the Space that owns a Bot."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"space_id": 42}},
    )

    space_id: int = Field(
        ge=1,
        description=(
            "Target Space identifier from GET /openapi/v1/spaces. Use the "
            "numeric personal-Space id to move the Bot back to personal space."
        ),
    )


class BotSpaceAssignment(BaseModel):
    """Persisted Bot ownership-Space assignment."""

    bot_id: str = Field(description="Bot whose owning Space was changed.")
    space_id: int = Field(description="Numeric identifier of the owning Space.")
    space_code: str = Field(description="Stable external code of the owning Space.")
    space_name: str = Field(description="Display name of the owning Space.")
    space_type: str = Field(
        description="Ownership model of the Space: PERSONAL or TEAM."
    )
    changed: bool = Field(
        description="False when the Bot already belonged to the requested Space."
    )


class BotUpdate(BaseModel):
    """Rename a bot and/or replace its description.

    Only these two fields are updatable: the engine is fixed at creation, the
    cluster is derived from the engine, and engine options are managed through
    the engine-config endpoints. Sending any other field fails validation with
    the field named in the error. Omit a field to leave it unchanged; explicit
    null is rejected.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"bot_name": "research-assistant-v2"}},
    )

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
    bot_name: str = Field(
        default=None,
        description="New name; omit to keep. Same rules as on create: "
        "non-blank, no '@', unique within the tenant.",
    )
    bot_desc: str = Field(default=None, description="New description; omit to keep.")


class BotAuthPending(BaseModel):
    """Returned (202) when bot creation needs user authorization first.

    Open whichever URL is populated to let the user complete authorization,
    then poll the bot's auth-status endpoint. Each URL is empty when the
    authorization service did not supply it — at least one is populated.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "bot_id": "20260813_a7k2m9p1",
                "iframe_url": "https://auth.example.com/passport/consent?flow=f-123",
                "redirect_url": "",
            }
        }
    )

    bot_id: str = Field(
        description="Identifier reserved for the bot being created. Pass it to "
        "the auth-status endpoint to poll and complete creation."
    )
    iframe_url: str = Field(
        default="",
        description="URL suitable for embedding in an iframe for the user to "
        "authorize in-page; empty when not offered.",
    )
    redirect_url: str = Field(
        default="",
        description="URL to redirect the user to for authorization; empty "
        "when not offered.",
    )


class BotAuthStatusPoll(BaseModel):
    """Poll-authorization request body: the attributes the bot was requested with.

    On the 202 create flow the bot is only actually created by this poll, so
    re-supply the attributes the create request carried. An omitted field falls
    back to the same default create would have applied, so omitting one the
    create named can produce a bot that contradicts the original request —
    always echo back what was sent. Every restriction create enforces is
    re-applied to the echoed values.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "bot_name": "research-assistant",
                "bot_desc": "Summarizes weekly industry news.",
                "engine": "openclaw",
                "cluster_name": "ACRA",
                "bot_type": "personal",
            }
        },
    )

    # Each field mirrors its BotCreate counterpart but is optional: this body
    # echoes an earlier create rather than stating a new request, and "omitted"
    # has to stay expressible so completion can apply the create-time defaults.
    engine: str | None = Field(
        default=None,
        description="Echo of the engine the bot was requested with. Required "
        "in practice: an omitted value falls back to the deployment default.",
    )
    cluster_name: ClusterName | None = Field(
        default=None,
        description="Echo of the cluster the bot was requested with; validated "
        "against the engine exactly as on create.",
    )
    bot_name: str | None = Field(
        default=None,
        description="Echo of the name the bot was requested with.",
    )
    bot_desc: str | None = Field(
        default=None,
        description="Echo of the description the bot was requested with.",
    )
    bot_type: BotType | None = Field(
        default=None,
        description="Echo of the bot type the bot was requested with; "
        "defaults to 'personal' when omitted.",
    )
    space_id: str | None = Field(
        default=None,
        description="Echo of the business space the bot was requested with; "
        "omitted resolves the caller's current space, exactly as on create.",
    )
    engine_properties: BotCreateEngineProperties | None = Field(
        default=None,
        description=(
            "Echo of the engine-specific properties the bot was requested with."
        ),
    )


class BotAuthStatus(BaseModel):
    """Authorization status of a pending bot creation."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "ISSUED",
                "message": None,
                "bot": {
                    "bot_id": "20260813_a7k2m9p1",
                    "bot_name": "research-assistant",
                    "bot_desc": "Summarizes weekly industry news.",
                    "engine": "openclaw",
                    "cluster_name": "ACRA",
                    "bot_type": "personal",
                    "status": "PENDING",
                    "owner_entity_id": "u_165137",
                },
            }
        }
    )

    # Typed `str`, not an enum: the state originates in the external
    # authorization service, which may add terminal states — an unknown value
    # must survive the round trip rather than 500.
    status: str = Field(
        description="Authorization state: 'PENDING' — the user has not "
        "finished authorizing, keep polling; 'ISSUED' — authorization granted "
        "and the bot is now created ('bot' is populated). Any other value "
        "(e.g. 'REJECTED', 'EXPIRED') is terminal and is answered as a 400 "
        "with the state kept in data.status."
    )
    # One producer today: the auth-status poll (either spelling) sets it when
    # the authorization service has no status for the bot yet — see
    # _complete_auth_status. The provider's own message is still not wired
    # through; that remains a separate change.
    message: str | None = Field(
        default=None,
        description="Human-readable note accompanying the status — for "
        "example, that the Passport is not ready yet while status is "
        "'PENDING'. Null when there is nothing to add.",
    )
    bot: Bot | None = Field(
        default=None,
        description="The created bot; present once status is 'ISSUED', "
        "null before that.",
    )


class BotStatus(BaseModel):
    """Runtime / device readiness of a bot."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "ACTIVE",
                "is_ready": True,
                "device_id": "device-8f2c91",
            }
        }
    )

    status: str = Field(description=_BOT_STATUS_DESC)
    is_ready: bool = Field(
        description="True once the bot can actually take work. Stricter than "
        "status == 'ACTIVE': an application-coding bot is not ready until its "
        "initial repository checkout has also succeeded."
    )
    device_id: str | None = Field(
        default=None,
        description="Identifier of the device (container) backing the bot; "
        "null while none is bound.",
    )


class Ceiling(BaseModel):
    """Bot creation quota for the selected business Space."""

    model_config = ConfigDict(json_schema_extra={"example": {"ceiling": 20}})

    ceiling: int = Field(
        description="Maximum number of non-deleted cloud Bots in this Space. "
        "A value of 0 or less means the limit is disabled."
    )


class BotQuotaExceededData(BaseModel):
    """Actionable capacity facts returned when a Space cannot accept a Bot."""

    space_id: str = Field(description="Identifier of the full target Space.")
    space_name: str = Field(description="Display name of the full target Space.")
    space_type: Literal["PERSONAL", "TEAM"] = Field(
        description="Whether the quota belongs to a Personal or Team Space."
    )
    ceiling: int = Field(gt=0, description="Configured Bot ceiling for the Space.")
    used: int = Field(ge=0, description="Current non-deleted cloud Bot count.")


class Passport(BaseModel):
    """A bot's platform-issued identity credential summary."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "bot_id": "20260813_a7k2m9p1",
                "passport_id": "20260813_a7k2m9p1",
            }
        }
    )

    bot_id: str = Field(description="The bot this Passport belongs to.")
    passport_id: str = Field(
        description="Identifier of the bot's Passport — the platform-issued "
        "identity credential downstream services authenticate the bot "
        "against. An opaque string whose shape is deployment-defined; it may "
        "equal the bot_id."
    )
    expire_at: str | None = Field(
        default=None, description="License expiration time when reported."
    )
    certificate_url: str | None = Field(
        default=None, description="License certificate URL when reported."
    )


class StartupScriptWrite(BaseModel):
    """PUT body for a bot's startup script — the script is the only field.

    The audit fields on the stored script are server-derived and cannot be
    supplied here; sending one fails validation rather than being silently
    dropped.
    """

    # Audit fields are deliberately absent: updated_by comes from the request
    # principal and updated_at from the row's own timestamp, so neither can be
    # asserted by a caller, and extra="forbid" means an attempt to send one
    # fails validation rather than being silently dropped with a 200.

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"script": "#!/bin/sh\npip install -q requests\n"}
        },
    )

    script: str = Field(
        description=(
            "Shell script run inside the bot's container on every container "
            "start, after the platform's own boot steps. Must be idempotent — "
            "it runs again on every start and the platform does not dedupe. "
            "Do not put secrets in the body."
        ),
    )


class StartupScript(BaseModel):
    """A bot's stored startup script. Every field is server-derived."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "bot_id": "20260813_a7k2m9p1",
                "script": "#!/bin/sh\npip install -q requests\n",
                "size_bytes": 34,
                "updated_by": "u_165137",
                "updated_at": "2026-08-01T09:12:04+00:00",
                "supported": True,
                "unsupported_reason": "",
            }
        }
    )

    bot_id: str = Field(description="The bot this script belongs to.")
    script: str = Field(description="Empty when the bot has no stored script.")
    size_bytes: int = Field(
        description="Size of the stored script in bytes; 0 when none is stored."
    )
    updated_by: str = Field(description="Empty when the bot has no stored script.")
    updated_at: datetime | None = Field(
        default=None,
        description="Null only when the bot has no stored script.",
    )
    supported: bool = Field(
        description=(
            "False when this bot's container cannot run a startup script. A "
            "write is refused in that case rather than stored."
        ),
    )
    unsupported_reason: str = Field(
        description="Empty when supported; otherwise names the cause.",
    )


class ConfigManifestWrite(BaseModel):
    """A configuration manifest submitted for storage."""

    model_config = ConfigDict(extra="forbid")

    document: str = Field(
        description=(
            "The manifest document (YAML). Stored and returned byte for byte — "
            "the `script` body's quoting and whitespace are preserved exactly."
        ),
    )


class ConfigManifestApplyStarted(BaseModel):
    """What `PUT …/config-manifest` did after storing the document (W8, §2.6).

    A write starts an apply of the stored document; this says whether it
    started. `RUNNING` carries the id to poll; `NOT_STARTED` carries why —
    `apply_in_progress` when another apply holds the bot, `not_started` when
    the apply could not be started for another reason. The document is stored
    in every case.
    """

    apply_id: str = Field(
        description="The apply to poll (`GET .../config-manifest/applies/{apply_id}`); "
        "empty when nothing started."
    )
    result: str = Field(description="`RUNNING` or `NOT_STARTED`.")
    reason: str | None = Field(
        default=None,
        description="Why nothing started: `apply_in_progress` or `not_started`.",
    )


class ConfigManifest(BaseModel):
    """A bot's stored configuration manifest. Every field but the document is
    server-derived."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "bot_id": "20260813_a7k2m9p1",
                "document": "schema_version: 1\nmanifest:\n  identity:\n"
                "    - type: SOUL.md\n      content: |\n        # Who I am\n",
                "size_bytes": 84,
                "schema_version": 1,
                "updated_by": "u_165137",
                "updated_at": "2026-08-31T09:12:04+00:00",
                "warnings": [],
            }
        }
    )

    bot_id: str = Field(description="The bot this manifest belongs to.")
    document: str = Field(
        description="Empty when the bot has no stored manifest.",
    )
    size_bytes: int = Field(
        description="Size of the stored document in bytes; 0 when none is stored."
    )
    schema_version: int | None = Field(
        default=None,
        description="Null only when the bot has no stored manifest.",
    )
    updated_by: str = Field(description="Empty when the bot has no stored manifest.")
    updated_at: datetime | None = Field(
        default=None,
        description="Null only when the bot has no stored manifest.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Non-fatal notes about the document just written — for example a "
            "source declared under `sources` that nothing references, a "
            "startup script that takes effect on the next start, or "
            "container-bound categories on a bot that is not ACTIVE. Always "
            "empty on a read."
        ),
    )
    apply: ConfigManifestApplyStarted | None = Field(
        default=None,
        description=(
            "The apply a write started (W8): `PUT` stores the document and then "
            "starts an apply of it. Absent on a read."
        ),
    )


class ManifestConstruct(BaseModel):
    """One thing a manifest can declare, and whether this bot accepts it."""

    kind: str = Field(
        description=(
            "`category` (one of the six under `manifest`), `section` (a "
            "top-level section such as `script`), or `source` (how an entry "
            "names its content)."
        )
    )
    name: str = Field(description="The construct's name within its kind.")
    supported: bool = Field(
        description="False when a document using this construct is refused."
    )
    reason: str = Field(description="Empty when supported; otherwise names the cause.")


class ConfigManifestCapabilities(BaseModel):
    """Which manifest constructs a bot accepts.

    Answered from the same resolver the write path refuses with, so this can
    never claim support for something a `PUT` then rejects.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "bot_id": "20260813_a7k2m9p1",
                "engine_type": "openclaw",
                "bot_type": "personal",
                "schema_versions": [1],
                "constructs": [
                    {
                        "kind": "category",
                        "name": "identity",
                        "supported": True,
                        "reason": "",
                    },
                    {
                        "kind": "source",
                        "name": "git",
                        "supported": False,
                        "reason": "git sources are resolved by the "
                        "named-and-git source work item (W7), which has not "
                        "landed",
                    },
                ],
            }
        }
    )

    bot_id: str = Field(description="The bot these capabilities describe.")
    engine_type: str = Field(description="The engine the answer was computed for.")
    bot_type: str = Field(description="The bot type the answer was computed for.")
    schema_versions: list[int] = Field(
        description="The `schema_version` values this deployment accepts."
    )
    constructs: list[ManifestConstruct] = Field(
        description="Every construct, supported or not, with its reason."
    )


class DataInitRequest(BaseModel):
    """Options for starting a bot's cold-start data initialization."""

    model_config = ConfigDict(extra="forbid")

    force: bool = Field(
        default=False,
        description="Run initialization again even if it previously completed.",
    )


DataInitStatus = Literal[
    "not_started", "pending_init", "in_progress", "completed", "failed"
]


class DataInitResult(BaseModel):
    """Public-safe cold-start data initialization state."""

    bot_id: str = Field(description="Bot whose data initialization state is reported.")
    status: DataInitStatus = Field(
        description=(
            "Current state. A trigger acknowledgement is `in_progress`; read the "
            "same resource with GET for the persisted state."
        )
    )
    message: str | None = Field(
        default=None, description="Additional trigger detail, when available."
    )
    started_at: datetime | None = Field(
        default=None,
        description="Initialization start time, when a running attempt recorded one.",
    )


# ── Bot inventory card surface ─────────────────────────────────────────────
# Card list returned by ``/openapi/v1/bots/all``. Action affordances are
# embedded in each item; there is no rich-card detail or standalone actions route.
# These Literals are trimmed siblings of the core enums in
# ``core.bot_inventory.types`` (kept as strings so a pydantic schema carries
# them as JSON enums without a circular import on the core module).

DeployMode = Literal["cloud", "local"]
BotInventoryKind = Literal["personal_cloud", "local", "service"]
DisplayState = Literal[
    "running",
    "pending",
    "failed",
    "dormant",
    "local_running",
    "local_offline",
    "local_pending",
    "local_failed",
    "service_draft",
    "service_deploying",
    "service_prestable",
    "service_staging",
    "service_online",
    "service_offline",
]
BotAction = Literal[
    "view",
    "chat",
    "edit",
    "delete",
    "restart",
    "restart_publish",
    "data_init",
    "activate",
    "open_folder",
    "passport",
    "engine_config",
    "runtime_logs",
    "engine_restart",
    "publish_staging",
    "publish_online",
    "cancel_staging",
    "upgrade",
    "offline",
    "retry",
]


class BusinessSpace(BaseModel):
    """Business-space reference surfaced on an inventory card."""

    space_id: str = Field(description="Unique identifier of the business space.")
    name: str = Field(description="Display name of the business space.")
    kind: str = Field(description="Space kind from the business-space owner.")


class BotInventoryItem(BaseModel):
    """Unified card for a personal cloud, local, or service bot."""

    bot_id: str = Field(description="Unique identifier of the bot.")
    card_id: str = Field(
        description="Stable card identity; service cards include the publication id."
    )
    bot_name: str = Field(description="Display name of the bot.")
    bot_desc: str = Field(
        description="Description of what the bot is for; may be empty."
    )
    engine: str = Field(description="Engine currently assigned to the bot.")
    bot_type: str = Field(
        description="Underlying bot type reported by the bot service."
    )
    kind: BotInventoryKind = Field(
        description="Inventory category used to render the bot card."
    )
    deploy_mode: DeployMode = Field(
        description="Whether the bot runs in the cloud or on a local device."
    )
    display_state: DisplayState = Field(
        description="Normalized lifecycle state used by the inventory view."
    )
    status: str = Field(
        description="Lifecycle status used by the owning view. Service cards expose draft/deploying/prestable/running/offline; other cards retain their owning service's status."
    )
    internal_status: str | None = Field(
        default=None,
        description="Stored service-publication status for diagnostics; null for non-service cards.",
    )
    publication_id: int | None = Field(
        default=None,
        description="Service publication id represented by this card; otherwise null.",
    )
    publication_version: int | None = Field(
        default=None,
        description="Service publication version represented by this card; otherwise null.",
    )
    live_version: int | None = Field(
        default=None, description="Currently running service version, when one exists."
    )
    owner_entity_id: str = Field(description="User who owns the bot.")
    space: BusinessSpace | None = Field(
        default=None, description="Business space containing the bot, when resolved."
    )
    template_type: str | None = Field(
        default=None,
        description="Template type of the bot, e.g. 'applicationCoding'; null for "
        "bots created without a template.",
    )
    template_config: dict | None = Field(
        default=None,
        description="Stored template snapshot, copied verbatim from the bot's "
        "creation input (no field filtering). Null without a template. May "
        "carry sensitive values the creator supplied (e.g. 'token', "
        "'bot_template_config.ext_config.thetaKey') and the server-managed "
        "'engine_form' marker — treat as sensitive.",
    )
    avatar_url: str | None = Field(
        default=None, description="Avatar URL for the bot, when configured."
    )
    machine_id: str | None = Field(
        default=None,
        description="Host device identifier for a local bot; otherwise null.",
    )
    mount_path: str | None = Field(
        default=None,
        description="Mounted workspace path for a local bot; otherwise null.",
    )
    passport_id: str | None = Field(
        default=None,
        description="Platform identity credential identifier, when issued.",
    )
    actions: list[BotAction] = Field(
        default_factory=list, description="Actions currently available for this bot."
    )
    disabled_actions: dict[str, str] | None = Field(
        default=None,
        description="Unavailable actions mapped to caller-facing reasons, when any.",
    )
    edit_lock: EditLock | None = Field(
        default=None,
        description="Current Bot-level collaborative edit lock for an operable service Bot; null for other cards.",
    )


class BotActivateResult(BaseModel):
    """Acknowledgement that a recycled personal cloud bot is reactivating."""

    bot_id: str = Field(description="Bot whose reactivation was started.")
    status: str = Field(description="Current reactivation status.")
    message: str | None = Field(
        default=None, description="Additional reactivation detail, when available."
    )
