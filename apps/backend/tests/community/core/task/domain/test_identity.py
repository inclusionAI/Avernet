import pytest

from agentclaw.community.core.task.domain.identity import (
    compose_bbs_author_identity,
    compose_bot_identity,
    is_bare_default_bot_identity,
    parse_scoped_bot_identity,
)


def test_compose_bot_identity_adds_owner_to_pure_bot_id():
    assert compose_bot_identity("default", "146836") == "default:146836"


def test_compose_bot_identity_rebuilds_stale_composite_owner():
    assert compose_bot_identity("default:old-owner", "146836") == "default:146836"


def test_compose_bot_identity_preserves_composite_without_explicit_owner():
    assert compose_bot_identity("default:146836") == "default:146836"


def test_compose_bbs_author_identity_requires_bot_id():
    with pytest.raises(ValueError, match="bot_id is required"):
        compose_bbs_author_identity(" ", "146836")


def test_compose_bbs_author_identity_scopes_only_default_bot():
    assert compose_bbs_author_identity("default", "146836") == "default:146836"
    assert compose_bbs_author_identity("bot-a", "146836") == "bot-a"


def test_is_bare_default_bot_identity_detects_only_owner_ambiguous_default():
    assert is_bare_default_bot_identity("default")
    assert is_bare_default_bot_identity(" default ")
    assert not is_bare_default_bot_identity("default:149844")
    assert not is_bare_default_bot_identity("bot-a")
    assert not is_bare_default_bot_identity("")


def test_compose_bbs_author_identity_requires_default_owner():
    with pytest.raises(ValueError, match="owner_user_id is required"):
        compose_bbs_author_identity("default", " ")


def test_parse_scoped_bot_identity_splits_owner_scoped_bot_id():
    assert parse_scoped_bot_identity("default:149844") == ("default", "149844")
    assert parse_scoped_bot_identity("bot-a:149844") == ("bot-a", "149844")


def test_parse_scoped_bot_identity_returns_none_for_bare_bot_id():
    assert parse_scoped_bot_identity("default") is None
    assert parse_scoped_bot_identity("") is None
