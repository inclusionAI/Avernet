"""A deleted bot takes its stored startup script with it (issue #926).

Bot deletion is a soft update, so nothing cascades to the script row. Before
this sweep the row outlived its bot indefinitely: plaintext executable content
retained past its owner, and — because ``create_bot`` accepts a caller-supplied
``bot_id`` and treats soft-deleted bots as absent — a script the *next* owner of
that id never wrote, executed on every one of their starts.

The purge is deliberately *not* one of the log-and-continue sweeps in
``cleanup_single_bot_data``. See ``purge_startup_script`` for why.
"""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.cleanup_service import (
    BotCleanupService,
)


def _make_service() -> BotCleanupService:
    return BotCleanupService(
        skill_repo=MagicMock(delete_by_bot_id=MagicMock(return_value=0)),
        skill_set_repo=MagicMock(delete_by_bot_id=MagicMock(return_value=0)),
        desired_state_repo=MagicMock(
            purge_bot_installations=MagicMock(
                return_value={"skills": 0, "mcps": 0}
            )
        ),
        startup_script_purge=MagicMock(delete=MagicMock(return_value=True)),
    )


class TestStartupScriptPurge:
    def test_it_deletes_the_row_for_that_bot(self):
        svc = _make_service()

        assert svc.purge_startup_script(entity_id="staff_user1", bot_id="bot1") is True
        svc._startup_script_purge.delete.assert_called_once_with(
            entity_id="staff_user1", bot_id="bot1"
        )

    def test_a_failure_propagates_rather_than_being_recorded(self):
        """The opposite of the skill sweeps, on purpose. Swallowing here would
        report a successful deletion while leaving executable content behind.
        """
        svc = _make_service()
        svc._startup_script_purge.delete.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            svc.purge_startup_script(entity_id="staff_user1", bot_id="bot1")

    def test_it_is_not_part_of_the_log_and_continue_sweeps(self):
        """Pins the separation itself: a caller running the generic cleanup must
        not think the script was handled. If someone folds the purge back into
        ``cleanup_single_bot_data``, its swallowing catch would silently apply
        to it again.
        """
        svc = _make_service()

        svc.cleanup_single_bot_data("bot1", "user1")

        svc._startup_script_purge.delete.assert_not_called()

    def test_the_purge_side_is_required_at_construction(self):
        """Optional, a composition that forgets to wire it would sweep skills
        and silently leave every script row behind — the exact bug this closes.
        """
        with pytest.raises(TypeError):
            BotCleanupService(  # type: ignore[call-arg]
                skill_repo=MagicMock(), skill_set_repo=MagicMock()
            )


def test_bot_cleanup_purges_installations_and_sets_before_owned_skill_assets():
    events = []
    service = _make_service()
    service._desired_state_repo.purge_bot_installations.side_effect = (
        lambda **kwargs: events.append(("installations", kwargs))
        or {"skills": 2, "mcps": 1}
    )
    service._skill_set_repo.delete_by_bot_id.side_effect = (
        lambda bot_id, owner_id: events.append(("sets", bot_id, owner_id)) or 1
    )
    service._skill_repo.delete_by_bot_id.side_effect = (
        lambda bot_id, owner_id: events.append(("skills", bot_id, owner_id)) or 3
    )

    result = service.cleanup_single_bot_data("default", "owner")

    assert events == [
        (
            "installations",
            {"bot_id": "default", "owner_id": "owner", "env": "dev"},
        ),
        ("sets", "default", "owner"),
        ("skills", "default", "owner"),
    ]
    assert result["skill_installations_deleted"] == 2
    assert result["mcp_installations_deleted"] == 1


def test_bot_cleanup_stops_when_installations_cannot_be_purged():
    service = _make_service()
    service._desired_state_repo.purge_bot_installations.side_effect = RuntimeError(
        "db unavailable"
    )

    result = service.cleanup_single_bot_data("default", "owner")

    assert result["errors"] == [
        "Cleanup installations error for bot default: db unavailable"
    ]
    service._skill_set_repo.delete_by_bot_id.assert_not_called()
    service._skill_repo.delete_by_bot_id.assert_not_called()
