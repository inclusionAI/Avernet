"""The operator gate's seam: either ladder shape answers, none dies silently.

``resolve_operator_level`` routes through the repository's own migration seam
(``resolve_operable_permission_level``), which probes the *effective* method
first and falls back to the legacy row ladder while in-place test doubles
migrate. A double too old to know the effective name once crashed into
AttributeError → NONE → a masked 404 indistinguishable from a refused
stranger — this file pins that the legacy shape still answers.
"""

from __future__ import annotations

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.engine_runtime.gate import require_bot_operator

BOT = "bot-1"
OWNER = "owner-1"
CALLER = "member-9"


class _LegacyCollaborators:
    """A pre-effective-ladder double: only ``get_permission_level`` exists."""

    def __init__(self, level: PermissionLevel) -> None:
        self._level = level
        self.calls: list[tuple[int, str, str]] = []

    def get_permission_level(self, bot_pk, user_id, owner_id, env=None):
        self.calls.append((bot_pk, user_id, owner_id))
        return self._level


def test_a_legacy_shaped_double_still_adjudicates_through_the_fallback():
    collab = _LegacyCollaborators(PermissionLevel.MEMBER)

    require_bot_operator(
        collab,
        bot={"id": 100, "owner_id": OWNER, "space_id": "22"},
        bot_id=BOT,
        caller_id=CALLER,
        owner_id=OWNER,
    )

    assert collab.calls == [(100, CALLER, OWNER)]


def test_a_legacy_double_below_the_bar_is_refused_as_missing():
    from agentclaw.community.core.bot_management.services.bot_service import (
        BotNotFoundError,
    )

    collab = _LegacyCollaborators(PermissionLevel.NONE)

    try:
        require_bot_operator(
            collab,
            bot={"id": 100, "owner_id": OWNER, "space_id": "22"},
            bot_id=BOT,
            caller_id=CALLER,
            owner_id=OWNER,
        )
    except BotNotFoundError:
        pass
    else:
        raise AssertionError("a below-bar caller must be refused")
    assert collab.calls, "the refusal must come from an adjudication, not a crash"
