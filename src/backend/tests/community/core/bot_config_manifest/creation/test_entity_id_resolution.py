"""The manifest's storage key must be the one the bot record will carry.

A drift here is silent: submission stores the document at one key, everything
afterwards looks for it at another, the apply finds no manifest and truthfully
reports that it applied nothing, and the bot comes up unconfigured with no error
anywhere.
"""
from __future__ import annotations

import inspect

from agentclaw.community.core.bot_config_manifest.creation import (
    resolve_manifest_entity_id,
)


def test_an_explicit_entity_id_is_used_as_given():
    assert (
        resolve_manifest_entity_id(spec_entity_id="u_owner", user_id="u_owner")
        == "u_owner"
    )


def test_an_absent_entity_id_takes_the_staff_default():
    assert (
        resolve_manifest_entity_id(spec_entity_id=None, user_id="u1")
        == "staff_u1"
    )


def test_the_rule_matches_the_one_create_bot_applies():
    """Pins the pairing rather than trusting the comment.

    ``BotService.create_bot`` resolves ``entity_id or f"staff_{user_id}"``. This
    module mirrors that rather than importing the creation graph, so the mirror
    needs holding: if that line ever changes, this fails instead of a bot
    silently coming up unconfigured.
    """
    from agentclaw.community.core.bot_management.services import bot_service

    source = inspect.getsource(bot_service.BotService.create_bot)
    assert 'resolved_entity_id = entity_id or f"staff_{user_id}"' in source, (
        "create_bot's entity_id rule changed; resolve_manifest_entity_id "
        "mirrors it and must change with it"
    )
