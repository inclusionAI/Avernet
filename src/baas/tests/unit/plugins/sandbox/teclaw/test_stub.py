"""Unit tests for StubTeClawBotPlugin.

Covers all 9 methods of the in-memory mock implementation:
- create_bot, destroy_bot, update_bot, restart_bot, get_bot
- resolve_http_conn_info, resolve_ws_conn_info, update_outbound_rule, close
- update_outbound_rule (storage + preservation + exposure)
"""

from __future__ import annotations

import pytest

from secbaas.community.plugins.sandbox.teclaw._stub import StubTeClawBotPlugin
from secbaas.community.spi.bot.teclaw._types import (
    BotCreateResult,
    BotDestroyResult,
    BotInfo,
    BotRestartResult,
    BotUpdateResult,
)


@pytest.fixture
def stub_plugin() -> StubTeClawBotPlugin:
    """Return a fresh StubTeClawBotPlugin instance for each test."""
    return StubTeClawBotPlugin()


# ---------------------------------------------------------------------------
# Test create_bot
# ---------------------------------------------------------------------------


class TestStubCreateBot:
    @pytest.mark.asyncio
    async def test_creates_bot_with_id_prefix(self, stub_plugin):
        """bot_id starts with 'stub-teclaw-'."""
        result = await stub_plugin.create_bot({"key": "val"})
        assert isinstance(result, BotCreateResult)
        assert result.teclaw_bot_id.startswith("stub-teclaw-")
        # 12 hex chars after prefix
        hex_part = result.teclaw_bot_id[len("stub-teclaw-") :]
        assert len(hex_part) == 12

    @pytest.mark.asyncio
    async def test_creates_bot_with_correct_status(self, stub_plugin):
        """status == 'ONLINE'."""
        result = await stub_plugin.create_bot({"key": "val"})
        assert result.status == "ONLINE"

    @pytest.mark.asyncio
    async def test_creates_bot_stores_config(self, stub_plugin):
        """get_bot returns same config that was passed to create_bot."""
        config = {"model": "gpt-4", "temperature": 0.7}
        create_result = await stub_plugin.create_bot(config)
        info = await stub_plugin.get_bot(create_result.teclaw_bot_id)
        assert info.teclaw_bot_config == config


# ---------------------------------------------------------------------------
# Test destroy_bot
# ---------------------------------------------------------------------------


class TestStubDestroyBot:
    @pytest.mark.asyncio
    async def test_destroy_returns_deleted_status(self, stub_plugin):
        """status == 'DELETED'."""
        create_result = await stub_plugin.create_bot({"key": "val"})
        result = await stub_plugin.destroy_bot(create_result.teclaw_bot_id)
        assert isinstance(result, BotDestroyResult)
        assert result.status == "DELETED"

    @pytest.mark.asyncio
    async def test_destroy_returns_correct_bot_id(self, stub_plugin):
        """Matches input bot_id."""
        create_result = await stub_plugin.create_bot({"key": "val"})
        bid = create_result.teclaw_bot_id
        result = await stub_plugin.destroy_bot(bid)
        assert result.teclaw_bot_id == bid

    @pytest.mark.asyncio
    async def test_destroy_unknown_id_is_lenient(self, stub_plugin):
        """Destroying an unknown bot_id does not raise."""
        result = await stub_plugin.destroy_bot("nonexistent-id")
        assert result.status == "DELETED"
        assert result.teclaw_bot_id == "nonexistent-id"


# ---------------------------------------------------------------------------
# Test update_bot
# ---------------------------------------------------------------------------


class TestStubUpdateBot:
    @pytest.mark.asyncio
    async def test_update_changes_stored_config(self, stub_plugin):
        """get_bot returns updated config after update_bot is called."""
        create_result = await stub_plugin.create_bot({"original": True})
        bid = create_result.teclaw_bot_id
        new_config = {"updated": True, "version": 2}
        await stub_plugin.update_bot(bid, new_config)
        info = await stub_plugin.get_bot(bid)
        assert info.teclaw_bot_config == new_config

    @pytest.mark.asyncio
    async def test_update_returns_correct_type(self, stub_plugin):
        """Returns BotUpdateResult."""
        create_result = await stub_plugin.create_bot({"key": "val"})
        bid = create_result.teclaw_bot_id
        result = await stub_plugin.update_bot(bid, {"new": "config"})
        assert isinstance(result, BotUpdateResult)
        assert result.teclaw_bot_id == bid
        assert result.status == "ONLINE"
        assert result.teclaw_bot_config == {"new": "config"}


# ---------------------------------------------------------------------------
# Test update_outbound_rule
# ---------------------------------------------------------------------------


class TestStubUpdateOutboundRule:
    @pytest.mark.asyncio
    async def test_stores_and_retrieves_outbound_rule(self, stub_plugin):
        """Create -> update rule -> get_bot round-trip."""
        create_result = await stub_plugin.create_bot({"key": "val"})
        bot_id = create_result.teclaw_bot_id
        rules = {"header_operation_rules": [{"action": "set"}]}
        result = await stub_plugin.update_outbound_rule(bot_id, rules)
        assert result is True
        info = await stub_plugin.get_bot(bot_id)
        assert info.outbound_rule == {"header_operation_rules": [{"action": "set"}]}

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self, stub_plugin):
        """update_outbound_rule returns True."""
        create_result = await stub_plugin.create_bot({"key": "val"})
        result = await stub_plugin.update_outbound_rule(
            create_result.teclaw_bot_id, {"header_operation_rules": []}
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_handles_unknown_bot_id(self, stub_plugin):
        """Calling update_outbound_rule for unknown bot_id does not raise."""
        rules = {"header_operation_rules": []}
        result = await stub_plugin.update_outbound_rule("unknown-bot-id", rules)
        assert result is True
        info = await stub_plugin.get_bot("unknown-bot-id")
        assert info.outbound_rule == {"header_operation_rules": []}

    @pytest.mark.asyncio
    async def test_preserves_outbound_rule_after_update_bot(self, stub_plugin):
        """update_bot must NOT wipe outbound_rule (setdefault preservation)."""
        create_result = await stub_plugin.create_bot({"key": "val"})
        bot_id = create_result.teclaw_bot_id
        rules = {"header_operation_rules": [{"value": "preserved-rule"}]}
        await stub_plugin.update_outbound_rule(bot_id, rules)
        await stub_plugin.update_bot(bot_id, {"new": "config"})
        info = await stub_plugin.get_bot(bot_id)
        assert info.outbound_rule == {
            "header_operation_rules": [{"value": "preserved-rule"}]
        }

    @pytest.mark.asyncio
    async def test_outbound_rule_defaults_to_none(self, stub_plugin):
        """get_bot returns outbound_rule=None when not set."""
        create_result = await stub_plugin.create_bot({"key": "val"})
        info = await stub_plugin.get_bot(create_result.teclaw_bot_id)
        assert info.outbound_rule is None


# ---------------------------------------------------------------------------
# Test restart_bot
# ---------------------------------------------------------------------------


class TestStubRestartBot:
    @pytest.mark.asyncio
    async def test_restart_returns_online(self, stub_plugin):
        """status == 'ONLINE'."""
        create_result = await stub_plugin.create_bot({"key": "val"})
        result = await stub_plugin.restart_bot(create_result.teclaw_bot_id)
        assert isinstance(result, BotRestartResult)
        assert result.status == "ONLINE"

    @pytest.mark.asyncio
    async def test_restart_uses_stored_config(self, stub_plugin):
        """Restart re-applies the stored config (config is preserved)."""
        config = {"feature": "enabled"}
        create_result = await stub_plugin.create_bot(config)
        bid = create_result.teclaw_bot_id
        await stub_plugin.restart_bot(bid)
        info = await stub_plugin.get_bot(bid)
        assert info.teclaw_bot_config == config

    @pytest.mark.asyncio
    async def test_restart_unknown_id_uses_empty_config(self, stub_plugin):
        """Restarting an unknown bot_id does not raise; uses empty config."""
        result = await stub_plugin.restart_bot("nonexistent")
        assert result.status == "ONLINE"
        # After restart, the bot gets stored with empty config
        info = await stub_plugin.get_bot("nonexistent")
        assert info.teclaw_bot_config == {}


# ---------------------------------------------------------------------------
# Test get_bot
# ---------------------------------------------------------------------------


class TestStubGetBot:
    @pytest.mark.asyncio
    async def test_get_bot_returns_stored_data(self, stub_plugin):
        """Returns correct bot_id, status for a stored bot."""
        create_result = await stub_plugin.create_bot({"key": "val"})
        bid = create_result.teclaw_bot_id
        info = await stub_plugin.get_bot(bid)
        assert isinstance(info, BotInfo)
        assert info.teclaw_bot_id == bid
        assert info.status == "ONLINE"
        assert info.teclaw_bot_config == {"key": "val"}

    @pytest.mark.asyncio
    async def test_get_bot_unknown_id(self, stub_plugin):
        """Returns status 'UNKNOWN' and bot_config None for unknown IDs."""
        info = await stub_plugin.get_bot("nonexistent")
        assert isinstance(info, BotInfo)
        assert info.teclaw_bot_id == "nonexistent"
        assert info.status == "UNKNOWN"
        assert info.teclaw_bot_config is None


# ---------------------------------------------------------------------------
# Test resolve_http_conn_info
# ---------------------------------------------------------------------------


class TestStubResolveHttpConnInfo:
    @pytest.mark.asyncio
    async def test_resolve_http_returns_correct_url(self, stub_plugin):
        """URL format: http://stub-teclaw:{port}{path}."""
        from secbaas.community.api.bot_runtime import HttpConnectionInfo

        result = await stub_plugin.resolve_http_conn_info("bot-1", 8080, "/api/invoke")
        assert isinstance(result, HttpConnectionInfo)
        assert result.http_url == "http://stub-teclaw:8080/api/invoke"
        assert result.target == "TECLAW_bot-1:8080"

    @pytest.mark.asyncio
    async def test_resolve_http_returns_token(self, stub_plugin):
        """token equals stub-jwt-{bot_id}; target uses canonical TECLAW_ format."""
        result = await stub_plugin.resolve_http_conn_info("my-bot", 443, "/")
        assert result.token == "stub-jwt-my-bot"
        assert result.target == "TECLAW_my-bot:443"

    @pytest.mark.asyncio
    async def test_resolve_http_uses_path_exactly(self, stub_plugin):
        """Path is used as-is in the URL."""
        result = await stub_plugin.resolve_http_conn_info("bot-x", 3000, "/custom/path")
        assert result.http_url == "http://stub-teclaw:3000/custom/path"
        assert result.target == "TECLAW_bot-x:3000"
        assert result.token == "stub-jwt-bot-x"

    @pytest.mark.asyncio
    async def test_resolve_http_target_with_template_id(self, stub_plugin):
        """Canonical target format with template_id: TECLAW_{bot_id}@{template_id}:{port}."""
        result = await stub_plugin.resolve_http_conn_info(
            "bot-tpl", 8080, "/api", template_id=42
        )
        assert result.target == "TECLAW_bot-tpl@42:8080"
        assert result.token == "stub-jwt-bot-tpl"

    @pytest.mark.asyncio
    async def test_resolve_http_target_without_template_id(self, stub_plugin):
        """Canonical target format without template_id: TECLAW_{bot_id}:{port}."""
        result = await stub_plugin.resolve_http_conn_info(
            "bot-no-tpl", 443, "/api", template_id=None
        )
        assert result.target == "TECLAW_bot-no-tpl:443"
        assert result.token == "stub-jwt-bot-no-tpl"


# ---------------------------------------------------------------------------
# Test resolve_ws_conn_info
# ---------------------------------------------------------------------------


class TestStubResolveWsConnInfo:
    @pytest.mark.asyncio
    async def test_resolve_ws_returns_correct_url(self, stub_plugin):
        """URL format: ws://stub-teclaw:{port}{path}."""
        from secbaas.community.api.bot_runtime import WsConnectionInfo

        result = await stub_plugin.resolve_ws_conn_info("bot-1", 8080, "/ws")
        assert isinstance(result, WsConnectionInfo)
        assert result.ws_url == "ws://stub-teclaw:8080/ws"

    @pytest.mark.asyncio
    async def test_resolve_ws_returns_target(self, stub_plugin):
        """Canonical target format: TECLAW_{bot_id}:{port}."""
        result = await stub_plugin.resolve_ws_conn_info("my-bot", 9999, "/api/ws")
        assert result.target == "TECLAW_my-bot:9999"

    @pytest.mark.asyncio
    async def test_resolve_ws_returns_token(self, stub_plugin):
        """token equals stub-jwt-{bot_id}."""
        result = await stub_plugin.resolve_ws_conn_info("my-bot", 443, "/")
        assert result.token == "stub-jwt-my-bot"

    @pytest.mark.asyncio
    async def test_resolve_ws_has_expires_at(self, stub_plugin):
        """expires_at is a datetime ~120s in the future."""
        from datetime import UTC, datetime, timedelta

        result = await stub_plugin.resolve_ws_conn_info("bot-t", 443, "/ws")
        assert isinstance(result.expires_at, datetime)
        now = datetime.now(UTC)
        assert result.expires_at > now
        assert result.expires_at < now + timedelta(seconds=130)


# ---------------------------------------------------------------------------
# Test close
# ---------------------------------------------------------------------------


class TestStubClose:
    @pytest.mark.asyncio
    async def test_close_is_noop(self, stub_plugin):
        """close() does not raise."""
        await stub_plugin.close()  # should not raise

    @pytest.mark.asyncio
    async def test_close_does_not_affect_stored_data(self, stub_plugin):
        """close() does not clear stored bots."""
        create_result = await stub_plugin.create_bot({"key": "val"})
        await stub_plugin.close()
        info = await stub_plugin.get_bot(create_result.teclaw_bot_id)
        assert info.status == "ONLINE"
        assert info.teclaw_bot_config == {"key": "val"}


# ---------------------------------------------------------------------------
# Test full lifecycle
# ---------------------------------------------------------------------------


class TestStubFullLifecycle:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self, stub_plugin):
        """Create -> Get -> Update -> Restart -> Get -> Destroy flow."""
        # Create
        create_result = await stub_plugin.create_bot({"stage": "initial"})
        bot_id = create_result.teclaw_bot_id
        assert create_result.status == "ONLINE"

        # Get - verify created
        info = await stub_plugin.get_bot(bot_id)
        assert info.status == "ONLINE"
        assert info.teclaw_bot_config == {"stage": "initial"}

        # Update
        update_result = await stub_plugin.update_bot(bot_id, {"stage": "updated"})
        assert update_result.status == "ONLINE"
        assert update_result.teclaw_bot_config == {"stage": "updated"}

        # Get - verify updated
        info = await stub_plugin.get_bot(bot_id)
        assert info.teclaw_bot_config == {"stage": "updated"}

        # Restart
        restart_result = await stub_plugin.restart_bot(bot_id)
        assert restart_result.status == "ONLINE"

        # Get - config preserved after restart
        info = await stub_plugin.get_bot(bot_id)
        assert info.teclaw_bot_config == {"stage": "updated"}

        # Destroy
        destroy_result = await stub_plugin.destroy_bot(bot_id)
        assert destroy_result.status == "DELETED"
        assert destroy_result.teclaw_bot_id == bot_id

        # Get after destroy - should be UNKNOWN
        info = await stub_plugin.get_bot(bot_id)
        assert info.status == "UNKNOWN"
        assert info.teclaw_bot_config is None
