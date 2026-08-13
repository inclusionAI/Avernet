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
