"""Test _get_path_params returns is_desktop based on ac_bots.bot_type."""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.adapters.http.dependencies import RequestContext
from agentclaw.community.adapters.http.skill_center.skills import _get_path_params
from agentclaw.community.adapters.http.skill_center.skillsets import _get_path_params as _gpp_skillsets


@pytest.fixture
def mock_ctx():
    return RequestContext(user_id="user_001", bot_id="bot_x")


def _make_bot_repo(bot_type: str = "personal"):
    repo = MagicMock()
    repo.get_by_id_and_owner.return_value = {
        "bot_id": "bot_x",
        "owner_id": "user_001",
        "active_engine": "openclaw",
        "bot_type": bot_type,
    }
    return repo


def test_returns_is_desktop_true_when_bot_type_is_desktop(mock_ctx):
    bot_repo = _make_bot_repo("desktop")
    result = _get_path_params(mock_ctx, bot_repo=bot_repo)
    assert len(result) == 5, f"expected 5-tuple, got {len(result)}"
    entity_id, bot_id, engine, entity_type, is_desktop = result
    assert is_desktop is True


def test_returns_is_desktop_false_when_bot_type_is_personal(mock_ctx):
    bot_repo = _make_bot_repo("personal")
    _, _, _, _, is_desktop = _get_path_params(mock_ctx, bot_repo=bot_repo)
    assert is_desktop is False


def test_returns_is_desktop_false_when_bot_type_is_service(mock_ctx):
    """Critical: service bots also have device_provider=baas but must NOT route to desktop path."""
    bot_repo = _make_bot_repo("service")
    _, _, _, _, is_desktop = _get_path_params(mock_ctx, bot_repo=bot_repo)
    assert is_desktop is False


def test_returns_is_desktop_false_when_bot_record_missing(mock_ctx):
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = None
    _, _, _, _, is_desktop = _get_path_params(mock_ctx, bot_repo=bot_repo)
    assert is_desktop is False


def test_returns_is_desktop_false_when_bot_type_missing(mock_ctx):
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"active_engine": "openclaw"}
    _, _, _, _, is_desktop = _get_path_params(mock_ctx, bot_repo=bot_repo)
    assert is_desktop is False


def test_lookup_failure_degrades_to_false(mock_ctx):
    """If the DB lookup raises, _get_path_params must not bubble — return is_desktop=False."""
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.side_effect = Exception("DB down")
    _, _, _, _, is_desktop = _get_path_params(mock_ctx, bot_repo=bot_repo)
    assert is_desktop is False


# ── Variant from skillsets.py (parallel definition) ──────────────────────

def test_skillsets_returns_is_desktop_true(mock_ctx):
    bot_repo = _make_bot_repo("desktop")
    result = _gpp_skillsets(mock_ctx, bot_repo=bot_repo)
    assert len(result) == 5
    assert result[4] is True


def test_skillsets_service_bot_is_not_desktop(mock_ctx):
    bot_repo = _make_bot_repo("service")
    result = _gpp_skillsets(mock_ctx, bot_repo=bot_repo)
    assert result[4] is False
