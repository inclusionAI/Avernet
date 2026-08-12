"""A deleted bot takes its stored startup script with it (issue #926).

Bot deletion is a soft update, so nothing cascades to the script row. Before
this sweep the row outlived its bot indefinitely: plaintext executable content
retained past its owner, and — because ``create_bot`` accepts a caller-supplied
``bot_id`` and treats soft-deleted bots as absent — a script the *next* owner of
that id never wrote, executed on every one of their starts.
"""
from unittest.mock import MagicMock

from agentclaw.community.core.bot_management.services.cleanup_service import (
    BotCleanupService,
)


def _make_service() -> BotCleanupService:
    svc = BotCleanupService(
        skill_repo=MagicMock(delete_by_bot_id=MagicMock(return_value=0)),
        skill_set_repo=MagicMock(delete_by_bot_id=MagicMock(return_value=0)),
        startup_script_purge=MagicMock(delete=MagicMock(return_value=True)),
    )
    return svc


class TestStartupScriptCleanup:
    def test_deleting_a_bot_deletes_its_script(self):
        svc = _make_service()

        result = svc.cleanup_single_bot_data("bot1", "user1", entity_id="staff_user1")

        svc._startup_script_purge.delete.assert_called_once_with(
            entity_id="staff_user1", bot_id="bot1"
        )
        assert result["startup_script_deleted"] is True

    def test_the_row_is_keyed_by_entity_not_by_owner(self):
        """``user_id`` only *usually* equals ``entity_id``; under a team entity
        it does not. Keying on the owner would miss the row for every team bot.
        """
        svc = _make_service()

        svc.cleanup_single_bot_data("bot1", "user1", entity_id="team_42")

        assert svc._startup_script_purge.delete.call_args.kwargs["entity_id"] == "team_42"

    def test_a_bot_with_no_entity_is_skipped_rather_than_guessed_at(self):
        """A bot with no entity id never had a script — the write path requires
        both halves of the key — so there is nothing to delete and no reason to
        invent an id to look one up by.
        """
        svc = _make_service()

        result = svc.cleanup_single_bot_data("bot1", "user1", entity_id="")

        svc._startup_script_purge.delete.assert_not_called()
        assert result["startup_script_deleted"] is False
        assert result["errors"] == []

    def test_a_purge_failure_is_recorded_but_does_not_block_the_delete(self):
        """Consistent with the skill and skill-set sweeps above it. Leaving one
        row behind is a smaller cost than a failed cleanup wedging the bot in a
        state where it cannot be deleted at all.
        """
        svc = _make_service()
        svc._startup_script_purge.delete.side_effect = RuntimeError("db down")

        result = svc.cleanup_single_bot_data("bot1", "user1", entity_id="staff_user1")

        assert result["startup_script_deleted"] is False
        assert any("startup_script" in e for e in result["errors"])

    def test_the_other_sweeps_still_run_when_the_script_purge_fails(self):
        svc = _make_service()
        svc._skill_repo.delete_by_bot_id.return_value = 3
        svc._startup_script_purge.delete.side_effect = RuntimeError("db down")

        result = svc.cleanup_single_bot_data("bot1", "user1", entity_id="staff_user1")

        assert result["skills_deleted"] == 3

    def test_the_purge_side_is_required_at_construction(self):
        """Optional, a composition that forgets to wire it would sweep skills
        and silently leave every script row behind — the exact bug this closes.
        """
        import pytest

        with pytest.raises(TypeError):
            BotCleanupService(  # type: ignore[call-arg]
                skill_repo=MagicMock(), skill_set_repo=MagicMock()
            )
