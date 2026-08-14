"""Rule 25 conformance — PassportPlugin.

Consumer under test: ``BotService`` and ``DesktopBotService`` both
call ``passport.apply_agent_passport(...)`` /
``apply_first_agent_passport(...)`` during bot creation flows. Seeding
those flows requires substantial fixtures (bot rows, user bindings,
device records), so the plugin's contract is observed through the
DI-bound instance the services hold.

The local ``LocalPassportPlugin`` returns mock envelopes that include
the bot_id in the token string (``mock_token_first_{bot_id}``) — a
fingerprint only the local impl produces.

Plugin-hit assertion: the consumer's call surface returns a token
matching the local impl's fingerprint. A consumer bypassing the
plugin would not produce that exact string.
"""
from __future__ import annotations

import pytest

from agentclaw.community.plugin_api.passport import PassportPlugin


def test_apply_first_agent_passport_returns_local_mock_envelope(world) -> None:
    plugin = world.get(PassportPlugin)
    result = plugin.apply_first_agent_passport(
        bot_id="bot_x",
        owner_workno="alice",
        mcp_codes=[],
    )
    assert result is not None
    # Fingerprint only the local impl produces.
    assert result["token"] == "mock_token_first_bot_x"
    assert result["agent_code"] == "local_bot_x"


def test_local_passport_accepts_explicit_target_env(world) -> None:
    plugin = world.get(PassportPlugin)

    result = plugin.apply_first_agent_passport(
        bot_id="bot_prod",
        owner_workno="alice",
        mcp_codes=[],
        target_env="prod",
    )

    assert result is not None
    assert result["token"] == "mock_token_first_bot_prod"
    assert (
        plugin.query_token("bot_prod", "alice", target_env="prod")
        == "mock_token_bot_prod"
    )


def test_local_passport_rejects_invalid_explicit_target_env(world) -> None:
    plugin = world.get(PassportPlugin)

    with pytest.raises(ValueError, match="target_env"):
        plugin.query_agent_passport("bot_x", "alice", target_env="staging")


def test_apply_agent_passport_returns_local_mock_envelope(world) -> None:
    plugin = world.get(PassportPlugin)
    result = plugin.apply_agent_passport(
        bot_id="bot_y",
        owner_workno="alice",
        mcp_codes=[],
    )
    assert result is not None
    assert result["token"] == "mock_token_nonfirst_bot_y"


# ── community impl (B4) — self-issued, deterministic, consent-free ──


def test_community_passport_issues_deterministic_envelope(community_world) -> None:
    plugin = community_world.get(PassportPlugin)
    result = plugin.apply_first_agent_passport(
        bot_id="bot_x",
        owner_workno="alice",
        mcp_codes=[],
    )
    assert result is not None
    # Non-empty token + agent_code, no consent step → create_bot never blocks.
    assert result["token"] == "community-passport-bot_x"
    assert result["agent_code"] == "bot_x"
    assert result["iframe_url"] is None
    # query_token agrees with the issued token (device bootstrap depends on it).
    assert plugin.query_token("bot_x", "alice") == "community-passport-bot_x"


def test_local_unfreeze_leaves_runtime_token_queryable(world) -> None:
    plugin = world.get(PassportPlugin)

    plugin.unfreeze_agent_passport(
        bot_id="bot_local",
        owner_workno="alice",
        reason="manual reactivate",
    )

    assert plugin.query_token("bot_local", "alice") == "mock_token_bot_local"


def test_community_unfreeze_leaves_runtime_token_queryable(community_world) -> None:
    plugin = community_world.get(PassportPlugin)

    plugin.unfreeze_agent_passport(
        bot_id="bot_community",
        owner_workno="alice",
        reason="manual reactivate",
    )

    assert (
        plugin.query_token("bot_community", "alice")
        == "community-passport-bot_community"
    )
