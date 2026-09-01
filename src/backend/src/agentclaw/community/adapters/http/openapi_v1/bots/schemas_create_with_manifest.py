"""Wire models for creating a bot from a manifest (W13, #1696).

Two shapes and one enum. The enum is the whole vocabulary of the poll, and it
appears on the poll's response and **nowhere else** — see
``BotCreateWithManifestAccepted`` for why that is a property of the models
rather than a convention.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..enums import _DocumentedEnum
from .schemas import Bot, BotCreate
from .schemas_config_manifest_apply import ConfigManifestApply


# Everything a class or field docstring says in this package is published
# verbatim into the OpenAPI document external tenants read, so the reasoning for
# the split below lives here rather than in the docstring:
#
# Three failures have to be tellable apart without reading prose. An invalid
# manifest never reaches this vocabulary at all — it is a 422 at submission,
# with no bot and no state. CREATE_FAILED means there is no usable bot and the
# manifest is beside the point. APPLY_FAILED means the bot is up and part of
# its configuration is missing. A single FAILED covering the last two was the
# real source of the "did I get a bot or not?" ambiguity: the name now carries
# the answer instead of the payload having to argue it.
class CreationState(_DocumentedEnum):
    """Where a creation stands. Reported by the status endpoint, and only there."""

    AWAITING_AUTHORIZATION = "AWAITING_AUTHORIZATION"
    AUTHORIZATION_REJECTED = "AUTHORIZATION_REJECTED"
    AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    CREATING = "CREATING"
    CREATE_FAILED = "CREATE_FAILED"
    APPLYING = "APPLYING"
    READY = "READY"
    APPLY_FAILED = "APPLY_FAILED"

    __descriptions__ = {
        "AWAITING_AUTHORIZATION": (
            "Waiting for the user to open the authorization link. Nothing has "
            "been created yet."
        ),
        "AUTHORIZATION_REJECTED": (
            "Terminal. The user declined, and no bot was created. The submitted "
            "manifest is deleted with the creation."
        ),
        "AUTHORIZATION_EXPIRED": (
            "Terminal. Nobody responded within the authorization window, and no "
            "bot was created. Distinct from a rejection: nothing was decided."
        ),
        "CREATING": (
            "Authorized. The bot record exists and its container is being "
            "provisioned."
        ),
        "CREATE_FAILED": (
            "Terminal. There is no usable bot — it could not be created, or no "
            "container ever came up. Nothing to do with the manifest."
        ),
        "APPLYING": (
            "The bot is up and the post-container part of the manifest is being "
            "applied."
        ),
        "READY": (
            "Terminal. The bot is up and the whole manifest landed. The response "
            "carries the bot and the apply report."
        ),
        "APPLY_FAILED": (
            "Terminal. The bot is up and running, and part of its configuration "
            "did not land. The response carries the bot as well as the report, "
            "so this is visibly not the same as CREATE_FAILED. Fix the manifest "
            "and apply it again; nothing needs recreating."
        ),
    }


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
