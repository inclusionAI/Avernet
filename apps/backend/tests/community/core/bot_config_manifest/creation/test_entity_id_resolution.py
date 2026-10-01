"""The manifest's storage key must be the one the bot record will carry.

A drift here is silent: submission stores the document at one key, everything
afterwards looks for it at another, the apply finds no manifest and truthfully
reports that it applied nothing, and the bot comes up unconfigured with no error
anywhere.

What makes the two agree is not a rule copied from one side to the other — it is
that both are handed the **same** ``BotCreateSpec.entity_id``. These cases pin
that, rather than a fallback neither side can reach.
"""
from __future__ import annotations

import inspect

from agentclaw.community.core.bot_config_manifest.creation import (
    resolve_manifest_entity_id,
)


def test_the_key_is_the_spec_entity_id_unchanged():
    assert resolve_manifest_entity_id(spec_entity_id="u_owner") == "u_owner"


def test_the_seam_takes_only_what_the_key_is_made_of():
    """No ``user_id``, and its absence is the point.

    This used to take one and default to ``staff_{user_id}``, mirroring
    ``create_bot``'s own ``entity_id or f"staff_{user_id}"``. Both fallbacks are
    unreachable on this path — ``entity_id`` is a required ``str`` on the spec
    and reaches both sides concrete — so the mirror kept nothing in step while
    suggesting it did. A parameter back here would mean someone had reintroduced
    a second way to derive the key.
    """
    params = inspect.signature(resolve_manifest_entity_id).parameters
    assert set(params) == {"spec_entity_id"}


def test_both_sides_are_handed_the_same_spec_entity_id():
    """The actual guarantee, pinned on the two call sites that must agree.

    ``persist`` keys the manifest by ``spec.entity_id``; the job's completion
    hands ``create_bot`` the same ``spec.entity_id``, carried through the task
    payload. If either ever read a different source, this fails — which is the
    only way the two keys can diverge now that neither fallback is live.
    """
    from agentclaw.community.core.bot_management import create_flow

    submit = inspect.getsource(create_flow.submit_bot_creation_with_manifest)
    assert "spec_entity_id=spec.entity_id" in submit, (
        "the manifest's storage key stopped coming from the spec"
    )

    # The job's completion rebuilds the spec from its payload and delegates to
    # the shared ``complete_bot_authorization``, which is where ``create_bot`` is
    # actually called — so the second half of the guarantee is pinned there, on
    # the function that makes the call, not on the module.
    complete = inspect.getsource(create_flow.complete_manifest_creation)
    assert "creation_spec_from_payload" in complete, (
        "the job stopped rebuilding its spec from the payload it was given"
    )
    assert "complete_bot_authorization(" in complete, (
        "the job stopped going through the shared completion, so the create_bot "
        "call this test pins below is no longer the one it reaches"
    )

    authorization = inspect.getsource(create_flow.complete_bot_authorization)
    assert "entity_id=spec.entity_id" in authorization, (
        "create_bot stopped being handed the spec's entity_id"
    )
