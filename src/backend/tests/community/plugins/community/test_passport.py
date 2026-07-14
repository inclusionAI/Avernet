"""Unit tests for the community SelfIssuedPassportPlugin (B4 T7).

Verifies the issuer is deterministic, always-ISSUED, consent-free, and satisfies
every load-bearing return the bot lifecycle depends on.
"""
from __future__ import annotations

import pytest

from agentclaw.community.plugins.community.passport import SelfIssuedPassportPlugin


@pytest.fixture
def passport():
    return SelfIssuedPassportPlugin()


def test_apply_first_is_consent_free_and_issues(passport):
    result = passport.apply_first_agent_passport("bot-1", "owner-1", ["mcp-a"])
    assert result["token"]  # non-empty → create_bot never blocks on auth
    assert result["agent_code"] == "bot-1"
    assert result["iframe_url"] is None
    assert result["redirect_url"] is None


def test_apply_agent_matches_first(passport):
    first = passport.apply_first_agent_passport("bot-9", "owner", [])
    again = passport.apply_agent_passport("bot-9", "owner", [])
    assert first["token"] == again["token"]
    assert first["agent_code"] == again["agent_code"]


def test_token_is_deterministic_and_non_empty(passport):
    t1 = passport.query_token("bot-2", "owner")
    t2 = passport.query_token("bot-2", "owner")
    assert t1 == t2 == "community-passport-bot-2"
    # The issued token from apply_* must match query_token for the same bot.
    assert passport.apply_first_agent_passport("bot-2", "o", [])["token"] == t1


def test_agent_code_stable_and_echoed(passport):
    issued = passport.apply_agent_passport("bot-3", "owner", [])
    queried = passport.query_agent_passport("bot-3", "owner")
    assert issued["agent_code"] == queried["agent_code"] == "bot-3"


def test_auth_status_always_issued(passport):
    status = passport.query_auth_status("bot-4", "owner")
    assert status["status"] == "ISSUED"
    assert status["token"] == "community-passport-bot-4"


def test_explicit_target_env_keeps_self_issued_semantics(passport):
    issued = passport.apply_first_agent_passport(
        "bot-prod", "owner", [], target_env="prod"
    )

    assert passport.query_token(
        "bot-prod", "owner", target_env="prod"
    ) == issued["token"]


def test_invalid_explicit_target_env_is_rejected(passport):
    with pytest.raises(ValueError, match="target_env"):
        passport.query_auth_status("bot-4", "owner", target_env="staging")


def test_query_passport_clis_empty(passport):
    assert passport.query_passport_clis("bot-5", "owner") == []


def test_no_op_writes_return_none(passport):
    assert passport.update_passport("bot-6", "owner", admins=["a"]) is None
    assert passport.destroy_passport("bot-6", "owner") is None


def test_update_passport_rejects_malformed_scope(passport):
    # A resource_scope missing required keys raises (parity with corp impl).
    with pytest.raises(ValueError):
        passport.update_passport(
            "bot-7", "owner", resource_scope={"mcp_codes": ["x"]}  # type: ignore[typeddict-item]
        )


def test_not_a_mock_seam():
    from agentclaw.community.plugins.local._mock_seam import MockSeam

    assert not issubclass(SelfIssuedPassportPlugin, MockSeam)
