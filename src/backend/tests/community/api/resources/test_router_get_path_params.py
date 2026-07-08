"""Tests for resources/router.py _get_path_params helper.

Covers owner_id_for_lookup resolution and engine_type resolution.
"""
from unittest.mock import MagicMock

from agentclaw.community.adapters.http.dependencies import RequestContext
from agentclaw.community.adapters.http.resources.router import _get_path_params


def _make_ctx(user_id="user_001", bot_id="bot_x"):
    return RequestContext(user_id=user_id, bot_id=bot_id)


def _make_repo(active_engine="moltis"):
    repo = MagicMock()
    repo.get_by_id_and_owner.return_value = {"active_engine": active_engine}
    repo.get_by_id.return_value = {"active_engine": active_engine}
    return repo


class TestOwnerIdForLookup:
    def test_entity_id_takes_priority(self):
        """When entity_id is provided, it is used as owner_id for engine lookup."""
        ctx = _make_ctx(user_id="viewer_999")
        repo = _make_repo("moltis")
        _get_path_params(ctx, entity_id="owner_001", bot_id="bot_x", bot_repo=repo)
        repo.get_by_id_and_owner.assert_called_with("bot_x", "owner_001")

    def test_ctx_user_id_used_when_no_entity_id(self):
        """When entity_id is None, ctx.user_id is used as owner_id."""
        ctx = _make_ctx(user_id="user_001")
        repo = _make_repo("openclaw")
        _get_path_params(ctx, entity_id=None, bot_id="bot_x", bot_repo=repo)
        repo.get_by_id_and_owner.assert_called_with("bot_x", "user_001")

    def test_empty_entity_id_falls_back_to_ctx(self):
        """Empty string entity_id falls back to ctx.user_id."""
        ctx = _make_ctx(user_id="user_001")
        repo = _make_repo("moltis")
        _get_path_params(ctx, entity_id="", bot_id="bot_x", bot_repo=repo)
        repo.get_by_id_and_owner.assert_called_with("bot_x", "user_001")


class TestEngineTypeResolution:
    def test_returns_bot_active_engine(self):
        ctx = _make_ctx()
        repo = _make_repo("moltis")
        result = _get_path_params(ctx, bot_id="bot_x", bot_repo=repo)
        assert result[2] == "moltis"

    def test_override_takes_precedence(self):
        ctx = _make_ctx()
        repo = _make_repo("moltis")
        result = _get_path_params(ctx, bot_id="bot_x", engine_type="custom", bot_repo=repo)
        assert result[2] == "custom"

    def test_default_bot_id_when_none_provided(self):
        ctx = _make_ctx(bot_id=None)
        repo = _make_repo("openclaw")
        result = _get_path_params(ctx, bot_id=None, bot_repo=repo)
        assert result[1] == "default"
