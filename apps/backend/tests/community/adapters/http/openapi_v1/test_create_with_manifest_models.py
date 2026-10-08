"""The wire models for creating a bot from a manifest (W13, #1696).

Three properties here are load-bearing rather than tidy, and each is a test
because none of them is visible in a diff that breaks it.

**The submit response has no state.** The eight-state vocabulary belongs to the
poll. If a state field ever appeared on submission, the shape alone would invite
a caller to branch on it — and the only honest value it could ever hold is
`AWAITING_AUTHORIZATION`, because at submission nothing has happened yet. A
terminal state returned from submission would be a lie the model made
expressible. Keeping the enum off that model makes the lie *unrepresentable*
rather than merely absent.

**The create body inherits.** A field added to `BotCreate` must arrive here for
free; a hand-maintained copy drifts, and the drift shows up as a creation
attribute that silently stops working through this path only.

**The poll takes nothing but its path.** No body, no query — so there is no
second place a manifest could be supplied, and the document that was validated
at submission is necessarily the document that gets applied.
"""

from __future__ import annotations

from agentclaw.community.adapters.http.openapi_v1.bots import (
    schemas_create_with_manifest as models,
)
from agentclaw.community.adapters.http.openapi_v1.bots.schemas import Bot, BotCreate
from agentclaw.community.adapters.http.openapi_v1.bots.schemas_config_manifest_apply import (
    ConfigManifestApply,
)

#: Spec D-6's vocabulary, written out rather than derived from the enum: a test
#: that reads the thing it is checking would accept any edit to it.
EXPECTED_STATES = {
    "AWAITING_AUTHORIZATION",
    "AUTHORIZATION_REJECTED",
    "AUTHORIZATION_EXPIRED",
    "CREATING",
    "CREATE_FAILED",
    "APPLYING",
    "READY",
    "APPLY_FAILED",
}


def test_creation_state_is_exactly_the_eight_states() -> None:
    assert {state.value for state in models.CreationState} == EXPECTED_STATES
    # The member name and its wire value are the same string, so a caller
    # reading either the enum or the JSON sees one vocabulary.
    assert all(
        state.name == state.value for state in models.CreationState
    )


def test_the_three_failures_stay_distinguishable() -> None:
    """The distinction the states exist for, asserted rather than described.

    "Did I get a bot?" must be answerable from the state alone. A single
    ``FAILED`` covering both would force a caller to read the report to find
    out, and the manifest failing does not mean the bot is missing.
    """
    assert models.CreationState.CREATE_FAILED != models.CreationState.APPLY_FAILED
    # The third failure — an invalid manifest — is deliberately *not* in this
    # vocabulary: it is a 422 at submission, before a bot_id exists to poll.
    assert not any(
        "MANIFEST" in state.value or "INVALID" in state.value
        for state in models.CreationState
    )


def test_create_body_is_the_ordinary_one_plus_the_manifest() -> None:
    inherited = set(BotCreate.model_fields)
    fields = set(models.BotCreateWithManifest.model_fields)

    assert fields == inherited | {"config_manifest"}
    assert issubclass(models.BotCreateWithManifest, BotCreate)
    # `extra="forbid"` is inherited too: a typo'd creation attribute is refused
    # here exactly as it is on the ordinary create.
    assert models.BotCreateWithManifest.model_config.get("extra") == "forbid"


def test_the_submit_response_has_no_state_field() -> None:
    """The property the module docstring calls structural. See this file's own.

    Checked two ways, because a state could arrive under another name: no field
    is *called* a state, and no field is *typed* as one.
    """
    accepted = models.BotCreateWithManifestAccepted

    assert "state" not in accepted.model_fields
    assert not any("state" in name for name in accepted.model_fields)
    assert not any(
        field.annotation is models.CreationState
        for field in accepted.model_fields.values()
    )
    # What it does carry: the id to poll with, and where to send the user.
    assert set(accepted.model_fields) == {"bot_id", "iframe_url", "redirect_url"}


def test_the_poll_response_carries_the_state_the_bot_and_the_report() -> None:
    fields = models.BotCreateWithManifestStatus.model_fields

    assert fields["state"].annotation is models.CreationState
    assert fields["bot"].annotation == (Bot | None)
    assert fields["apply"].annotation == (ConfigManifestApply | None)
    # Both are optional, because the early states have neither yet; only the
    # state and the id are always present.
    assert fields["bot"].default is None
    assert fields["apply"].default is None
    assert fields["state"].is_required()
    assert fields["bot_id"].is_required()


def test_the_poll_has_no_request_model() -> None:
    """`bot_id` in the path is the poll's whole input.

    Asserted as an absence: this module publishes exactly one request shape, the
    submission. A poll body would be somewhere a manifest could be re-supplied,
    and then "validated once, applied as validated" would stop being structural.
    """
    published = set(models.__all__)

    assert published == {
        "BotCreateWithManifest",
        "BotCreateWithManifestAccepted",
        "BotCreateWithManifestStatus",
        "CreationState",
    }
