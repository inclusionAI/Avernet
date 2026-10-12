"""The operator gate's seam: an unresolvable ladder is refused, loudly.

``resolve_operator_level`` routes through the explicit ladder — a service
that has not grown ``get_explicit_permission_level`` is an unresolvable
authority, not a shape to fall back on: falling back would answer with the
Space-synthesized level, the exact answer the edit/operations domain
refuses. The gate refuses the caller (fail closed, masked 404) and logs
the wiring fault; a service that answers at or above the bar is served
through its own adjudication.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.engine_runtime.gate import require_bot_operator

BOT = "bot-1"
OWNER = "owner-1"
CALLER = "member-9"

BOT_ROW = {"id": 100, "owner_id": OWNER, "space_id": "22"}


class _LegacyCollaborators:
    """A pre-explicit-ladder double: only ``get_permission_level`` exists."""

    def __init__(self, level: PermissionLevel) -> None:
        self._level = level
        self.calls: list[tuple[int, str, str]] = []

    def get_permission_level(self, bot_pk, user_id, owner_id, env=None):
        self.calls.append((bot_pk, user_id, owner_id))
        return self._level


class _ExplicitCollaborators:
    """The explicit-ladder shape: only ``get_explicit_permission_level``."""

    def __init__(self, level: PermissionLevel) -> None:
        self._level = level
        self.calls: list[tuple[int, str]] = []

    def get_explicit_permission_level(self, *, bot, user_id, env=None):
        bot_pk = int(bot.get("id") or 0)
        self.calls.append((bot_pk, user_id))
        return self._level


def _require(collaborators):
    require_bot_operator(
        collaborators,
        bot=BOT_ROW,
        bot_id=BOT,
        caller_id=CALLER,
        owner_id=OWNER,
    )


def test_a_legacy_shaped_double_is_refused_not_fallback_adjudicated(caplog):
    """The fallback direction was the review's finding: a service missing
    the explicit ladder must not quietly answer with its old shape."""
    from agentclaw.community.core.bot_management.services.bot_service import (
        BotNotFoundError,
    )

    collab = _LegacyCollaborators(PermissionLevel.MEMBER)

    with pytest.raises(BotNotFoundError), caplog.at_level("WARNING"):
        _require(collab)

    assert collab.calls == []  # never consulted: no fallback adjudicates
    assert any(
        "no explicit ladder" in record.message for record in caplog.records
    ), "the refusal must log the wiring fault, not just deny"


def test_a_double_below_the_bar_is_refused_after_adjudication():
    from agentclaw.community.core.bot_management.services.bot_service import (
        BotNotFoundError,
    )

    collab = _ExplicitCollaborators(PermissionLevel.NONE)

    with pytest.raises(BotNotFoundError):
        _require(collab)

    assert collab.calls, "the refusal must come from an adjudication, not a crash"


def test_a_member_row_on_the_explicit_ladder_is_an_operator():
    """The explicit twin of the old served test: a real collaborator row at
    MEMBER holds the operator channel, adjudicated through the new ladder."""
    collab = _ExplicitCollaborators(PermissionLevel.MEMBER)

    _require(collab)

    assert collab.calls == [(100, CALLER)]
