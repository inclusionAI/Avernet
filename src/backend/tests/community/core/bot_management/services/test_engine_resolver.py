"""Tests for resolve_engine_for_bot fallback logic.

Verifies:
1. Override takes precedence over everything
2. owner_id exact match (Step 1) works for owner
3. Fallback to get_by_id (Step 2) works for collaborators on non-default bots
4. No fallback for bot_id="default" — prevents cross-user data leak
5. DEFAULT_ENGINE_TYPE returned when bot not found
"""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.engine_resolver import resolve_engine_for_bot
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE


def _make_repo(owner_bot=None, any_bot=None):
    """Create a mock BotRepository.

    Args:
        owner_bot: return value for get_by_id_and_owner (exact match)
        any_bot: return value for get_by_id (fallback, no owner check)
    """
    repo = MagicMock()
    repo.get_by_id_and_owner.return_value = owner_bot
    repo.get_by_id.return_value = any_bot
    return repo


class TestOverrideTakesPrecedence:
    def test_override_returned_directly(self):
        repo = _make_repo()
        result = resolve_engine_for_bot(
            bot_id="bot_x", owner_id="user_1", override="custom_engine", bot_repo=repo
        )
        assert result == "custom_engine"
        repo.get_by_id_and_owner.assert_not_called()
        repo.get_by_id.assert_not_called()

    def test_empty_override_is_ignored(self):
        bot = {"active_engine": "moltis"}
        repo = _make_repo(owner_bot=bot)
        result = resolve_engine_for_bot(
            bot_id="bot_x", owner_id="user_1", override="", bot_repo=repo
        )
        assert result == "moltis"


class TestOwnerExactMatch:
    def test_owner_match_returns_active_engine(self):
        bot = {"active_engine": "moltis"}
        repo = _make_repo(owner_bot=bot)
        result = resolve_engine_for_bot(
            bot_id="bot_x", owner_id="owner_1", bot_repo=repo
        )
        assert result == "moltis"
        repo.get_by_id_and_owner.assert_called_once_with("bot_x", "owner_1")
        repo.get_by_id.assert_not_called()

    def test_owner_match_no_active_engine_returns_default(self):
        bot = {"active_engine": None}
        repo = _make_repo(owner_bot=bot)
        result = resolve_engine_for_bot(
            bot_id="bot_x", owner_id="owner_1", bot_repo=repo
        )
        assert result == DEFAULT_ENGINE_TYPE


class TestCollaboratorFallback:
    def test_collaborator_falls_back_to_get_by_id(self):
        """Collaborator's owner_id doesn't match → Step 1 returns None → fallback to get_by_id."""
        bot = {"active_engine": "moltis", "owner_id": "real_owner"}
        repo = _make_repo(owner_bot=None, any_bot=bot)
        result = resolve_engine_for_bot(
            bot_id="bot_x", owner_id="collaborator_1", bot_repo=repo
        )
        assert result == "moltis"
        repo.get_by_id_and_owner.assert_called_once_with("bot_x", "collaborator_1")
        repo.get_by_id.assert_called_once_with("bot_x")

    def test_no_owner_id_goes_directly_to_fallback(self):
        """No owner_id provided → skip Step 1, go to get_by_id for non-default bot."""
        bot = {"active_engine": "openclaw"}
        repo = _make_repo(any_bot=bot)
        result = resolve_engine_for_bot(
            bot_id="bot_x", owner_id=None, bot_repo=repo
        )
        assert result == "openclaw"
        repo.get_by_id_and_owner.assert_not_called()
        repo.get_by_id.assert_called_once_with("bot_x")


class TestDefaultBotNoFallback:
    def test_default_bot_no_fallback_when_owner_mismatch(self):
        """bot_id='default' must NOT fallback — would get another user's data."""
        repo = _make_repo(owner_bot=None, any_bot={"active_engine": "moltis"})
        result = resolve_engine_for_bot(
            bot_id="default", owner_id="user_1", bot_repo=repo
        )
        assert result == DEFAULT_ENGINE_TYPE
        repo.get_by_id_and_owner.assert_called_once_with("default", "user_1")
        repo.get_by_id.assert_not_called()

    def test_default_bot_owner_match_works(self):
        """bot_id='default' with correct owner_id → Step 1 hits."""
        bot = {"active_engine": "moltis"}
        repo = _make_repo(owner_bot=bot)
        result = resolve_engine_for_bot(
            bot_id="default", owner_id="owner_1", bot_repo=repo
        )
        assert result == "moltis"
        repo.get_by_id.assert_not_called()

    def test_default_bot_no_owner_id_returns_default(self):
        """bot_id='default' without owner_id → cannot fallback → DEFAULT."""
        repo = _make_repo(any_bot={"active_engine": "moltis"})
        result = resolve_engine_for_bot(
            bot_id="default", owner_id=None, bot_repo=repo
        )
        assert result == DEFAULT_ENGINE_TYPE
        repo.get_by_id.assert_not_called()


class TestEdgeCases:
    def test_no_bot_id_returns_default(self):
        repo = _make_repo()
        assert resolve_engine_for_bot(bot_id=None, bot_repo=repo) == DEFAULT_ENGINE_TYPE
        assert resolve_engine_for_bot(bot_id="", bot_repo=repo) == DEFAULT_ENGINE_TYPE

    def test_both_lookups_return_none(self):
        repo = _make_repo(owner_bot=None, any_bot=None)
        result = resolve_engine_for_bot(
            bot_id="bot_x", owner_id="user_1", bot_repo=repo
        )
        assert result == DEFAULT_ENGINE_TYPE

    def test_exception_in_lookup_returns_default(self):
        repo = MagicMock()
        repo.get_by_id_and_owner.side_effect = RuntimeError("DB error")
        result = resolve_engine_for_bot(
            bot_id="bot_x", owner_id="user_1", bot_repo=repo
        )
        assert result == DEFAULT_ENGINE_TYPE

    def test_empty_active_engine_returns_default(self):
        bot = {"active_engine": ""}
        repo = _make_repo(owner_bot=bot)
        result = resolve_engine_for_bot(
            bot_id="bot_x", owner_id="owner_1", bot_repo=repo
        )
        assert result == DEFAULT_ENGINE_TYPE
