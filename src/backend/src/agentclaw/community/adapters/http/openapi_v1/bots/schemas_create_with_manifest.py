"""Wire models for creating a bot from a manifest (W13, #1696).

Two shapes and one enum. The enum is the whole vocabulary of the poll, and it
appears on the poll's response and **nowhere else** — see
:class:`BotCreateWithManifestAccepted` for why that is a property of the models
rather than a convention.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .schemas import Bot, BotCreate
from .schemas_config_manifest_apply import ConfigManifestApply


class CreationState(StrEnum):
    """Where a creation stands. Reported by the poll, and only by the poll.

    Three failures, kept apart on purpose, because a caller has to tell them
    apart without reading prose:

    * an **invalid manifest** never reaches this vocabulary at all — it is a
      `422` at submission, with no bot and no state;
    * :attr:`CREATE_FAILED` — there is no usable bot, and the manifest is beside
      the point;
    * :attr:`APPLY_FAILED` — the bot is up and part of its configuration is
      missing.

    A single ``FAILED`` covering the last two was the real source of the "did I
    get a bot or not?" ambiguity: the name now carries the answer instead of the
    payload having to argue it.
    """

    AWAITING_AUTHORIZATION = "AWAITING_AUTHORIZATION"
    AUTHORIZATION_REJECTED = "AUTHORIZATION_REJECTED"
    AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    CREATING = "CREATING"
    CREATE_FAILED = "CREATE_FAILED"
    APPLYING = "APPLYING"
    READY = "READY"
    APPLY_FAILED = "APPLY_FAILED"


class BotCreateWithManifest(BotCreate):
    """Create a bot, with its configuration, in one request.

    Every field of the ordinary create body, plus the manifest. Inheriting rather
    than restating them is deliberate: a field added to creation must not silently
    be missing here, and two hand-maintained copies would drift.
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
                "config_manifest": (
                    "schema_version: 1\n"
                    "script:\n"
                    '  body: "echo provisioned"\n'
                ),
            }
        },
    )

    config_manifest: str = Field(
        description=(
            "The manifest document (YAML), exactly as `PUT "
            "/bots/{bot_id}/config-manifest` accepts it — with one difference: "
            "a category no materializer can apply yet is **refused here** "
            "rather than stored inert, because accepting it would mean "
            "authorizing, creating the bot, and only then failing to configure "
            "it.\n\n"
            "Submit it once. The poll never accepts it again, so the manifest "
            "that was validated is always the manifest that gets applied.\n\n"
            "Iteration 1 rule: a manifest's `script` must not depend on "
            "anything else the same manifest declares. On a first boot the "
            "script is baked into the start command and runs before any other "
            "category has been delivered."
        ),
    )


class BotCreateWithManifestAccepted(BaseModel):
    """What submission answers with: an id, and where to send the user.

    **No state field, and that is structural rather than stylistic.** The state
    vocabulary lives on the poll's response only, so no terminal value can ever
    be returned by submission — a caller that has just submitted is, by
    construction, awaiting authorization.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "bot_id": "20260813_a7k2m9p1",
                "iframe_url": "https://auth.example.com/passport/consent?flow=f-1",
                "redirect_url": "",
            }
        },
    )

    bot_id: str = Field(
        description="The allocated bot id. Poll the status endpoint with it."
    )
    iframe_url: str = Field(
        description=(
            "Embeddable authorization URL, or empty. Passport returns this or "
            "`redirect_url`, and which one is not predictable — use whichever "
            "is non-empty."
        )
    )
    redirect_url: str = Field(
        description="Full-page authorization URL, or empty. See `iframe_url`."
    )


class BotCreateWithManifestStatus(BaseModel):
    """Where a creation stands, and everything a caller needs at the end.

    The terminal states carry **both** the report and the bot. The bot matters
    most on `APPLY_FAILED`: a caller must be able to see that it exists and is
    running, rather than infer it from a word.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "state": "READY",
                "bot_id": "20260813_a7k2m9p1",
                "iframe_url": "",
                "redirect_url": "",
                "message": "",
            }
        },
    )

    state: CreationState = Field(description="Where the creation stands.")
    bot_id: str = Field(description="The bot this creation is for.")
    iframe_url: str = Field(
        default="",
        description=(
            "Authorization URL while `AWAITING_AUTHORIZATION`; empty afterwards."
        ),
    )
    redirect_url: str = Field(
        default="", description="See `iframe_url`."
    )
    bot: Bot | None = Field(
        default=None,
        description=(
            "The created bot, once it exists. Present at `READY` **and at "
            "`APPLY_FAILED`** — the manifest failing never touches the bot "
            "record, and the response says so rather than leaving it to be "
            "inferred."
        ),
    )
    apply: ConfigManifestApply | None = Field(
        default=None,
        description=(
            "The apply report, at both terminal states. Names every entry that "
            "did and did not land, across both phases — the pre-container "
            "phase's `script` is carried into it, so nothing looks as though it "
            "vanished."
        ),
    )
    message: str = Field(
        default="",
        description=(
            "Why, when a state needs saying more than naming — a provisioning "
            "failure, or an authorization the user declined."
        ),
    )


__all__ = [
    "BotCreateWithManifest",
    "BotCreateWithManifestAccepted",
    "BotCreateWithManifestStatus",
    "CreationState",
]
