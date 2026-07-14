"""Tests for MCPSyncService — provider-blind delivery via resolver + dispatcher.

MCPSyncService no longer branches on ``device_provider``: it obtains a per-bot
``DeviceSyncPlugin`` from
``DeviceContextResolver.resolve_for_bot → DeviceSyncDispatcher.dispatch(ctx)``
and calls the MCP methods on it. The plugin decides *how* to deliver
(arca/baas per-MCP, teclaw whole-artifact, local no-op) — that's covered by
the plugin/contract tests. Here we assert the service routes correctly, maps
``DeviceNotBoundError`` / ``UnknownProviderError`` to the "missing device"
error, and that the multi-bot batch rolls back uniformly (Option B — a teclaw
delivery failure is NOT best-effort).

The plugin's MCP methods are **synchronous** (the service wraps them in
``asyncio.to_thread``), so the doubles use plain ``MagicMock``.
"""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.device_context import (
    DeviceContext,
    DeviceNotBoundError,
)
from agentclaw.community.core.mcp.services.sync_service import MCPSyncService


def _make_mcp_provider(mcps=None, all_mcps=None, summary=None):
    """Create a mock BotMCPProvider."""
    provider = MagicMock()
    provider.collect_bot_active_mcps.return_value = mcps or []
    provider.collect_bot_mcps.return_value = all_mcps or mcps or []
    provider.get_active_skill_sets_mcp_summary.return_value = summary or ([], [])
    return provider


def _make_plugin(**overrides):
    """A per-bot DeviceSyncPlugin double. MCP methods are SYNC (run via to_thread)."""
    plugin = MagicMock()
    plugin.sync_all_mcp_servers = MagicMock(return_value=True)
    plugin.sync_single_mcp = MagicMock(return_value=True)
    plugin.sync_remove_mcp = MagicMock(return_value=True)
    plugin.has_mcp = MagicMock(return_value=True)
    for k, v in overrides.items():
        setattr(plugin, k, v)
    return plugin


def _make_ctx(bot_id: str = "bot1", user_id: str = "u1") -> DeviceContext:
    """工厂:稳定 DeviceContext mock。"""
    return DeviceContext(
        provider="baas",
        conn_info={"engine_type": "openclaw"},
        binding_id=42,
        bot_id=bot_id,
        user_id=user_id,
    )


def _make_resolver_and_dispatcher(plugin=None, *, unavailable=False):
    """Build a (resolver, dispatcher, plugin) triple to inject."""
    resolver = MagicMock()
    dispatcher = MagicMock()
    if unavailable:
        resolver.resolve_for_bot.side_effect = DeviceNotBoundError("no binding")
    else:
        resolver.resolve_for_bot.return_value = _make_ctx()
    actual_plugin = plugin or _make_plugin()
    dispatcher.dispatch.return_value = actual_plugin
    return resolver, dispatcher, actual_plugin


def _make_sync_service(
    mcp_provider=None,
    passport_update=None,
    mcp_config_service=None,
    bot_repository=None,
    mcp_center=None,
    resolver=None,
    dispatcher=None,
    plugin=None,
):
    """Create MCPSyncService with all dependencies mocked.

    ``resolver`` / ``dispatcher`` 替代旧 ``device_sync_supplier`` —— 取自
    ``_make_resolver_and_dispatcher`` 工厂。
    """
    provider = mcp_provider or _make_mcp_provider()
    service = MCPSyncService.__new__(MCPSyncService)
    service._mcp_provider_factory = lambda: provider
    service._mcp_provider_cached = provider
    service.mcp_center = mcp_center or MagicMock()
    service.user_mcp_config_repo = MagicMock()
    service.passport_update = passport_update or MagicMock()
    service.mcp_config_service = mcp_config_service or MagicMock()
    service.mcp_config_service.build_mcp_sync_payload.return_value = (
        None, {}, "PROD", None
    )
    service.bot_repository = bot_repository or MagicMock()
    if resolver is None or dispatcher is None:
        _r, _d, _ = _make_resolver_and_dispatcher(plugin=plugin)
        service._resolver_provider = (lambda r=(resolver or _r): r)
        service._device_sync_dispatcher_provider = (lambda d=(dispatcher or _d): d)
    else:
        service._resolver_provider = lambda: resolver
        service._device_sync_dispatcher_provider = lambda: dispatcher
    return service


class TestRefreshMcpScope:
    """Test refresh_mcp_scope: scope declaration + passport update."""

    @pytest.mark.asyncio
    async def test_updates_passport_when_scope_ok(self):
        """When scope declaration succeeds, passport should be updated
        with all active MCP codes and the latest AgentPass CLI scope."""
        mcps = [
            {"server_code": "mcp.test.1", "name": "MCP 1"},
            {"server_code": "mcp.test.2", "name": "MCP 2"},
        ]
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = [
            {"cli_code": "cli.keep", "cli_name": "Keep CLI", "cli_desc": None}
        ]

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=mcps),
            passport_update=passport_update,
        )

        result = await service.refresh_mcp_scope(
            user_id="user1",
            entity_id="100",
            bot_id="bot1",
            entity_type="staff",
            engine_type="openclaw",
        )

        assert result["success"] is True
        passport_update.update_passport.assert_called_once()
        call_kwargs = passport_update.update_passport.call_args
        resource_scope = call_kwargs.kwargs["resource_scope"]
        assert "mcp.test.1" in resource_scope["mcp_codes"]
        assert "mcp.test.2" in resource_scope["mcp_codes"]
        assert resource_scope["cli_items"] == [
            {"cli_code": "cli.keep", "cli_name": "Keep CLI", "cli_desc": None}
        ]

    @pytest.mark.asyncio
    async def test_updates_passport_without_local_mcp_scope(self):
        """Local/stdin MCPs stay in device scope but are excluded from AgentPass."""
        mcps = [
            {"server_code": "mcp.remote.1", "name": "Remote MCP"},
            {"server_code": "hitl", "name": "HITL MCP"},
            {
                "server_code": "mcp.inline.local",
                "name": "Inline Local MCP",
                "runMode": "LOCAL",
                "stdioConfigs": [{"command": "node", "arguments": ["server.js"]}],
            },
        ]
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = []
        plugin = _make_plugin()
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=mcps),
            passport_update=passport_update,
            resolver=resolver,
            dispatcher=dispatcher,
        )

        result = await service.refresh_mcp_scope(
            user_id="user1",
            entity_id="100",
            bot_id="bot1",
            entity_type="staff",
            engine_type="openclaw",
        )

        assert result["success"] is True
        plugin.sync_all_mcp_servers.assert_called_once_with(mcps)
        resource_scope = passport_update.update_passport.call_args.kwargs[
            "resource_scope"
        ]
        assert resource_scope["mcp_codes"] == ["mcp.remote.1"]

    @pytest.mark.asyncio
    async def test_merges_default_cli_items_when_syncing_aicoding_scope(self):
        """MCP scope refresh should not clear engine default CLI grants."""
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = []
        bot_repository = MagicMock()
        bot_repository.get_by_id_and_owner.return_value = {
            "bot_name": "AICoding Bot",
            "bot_desc": "desc",
            "active_engine": "aicoding",
            "template_type": "personalCoding",
        }

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.test.1"}]),
            passport_update=passport_update,
            bot_repository=bot_repository,
        )

        result = await service.refresh_mcp_scope(
            user_id="user1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="aicoding",
        )

        assert result["success"] is True
        resource_scope = passport_update.update_passport.call_args.kwargs[
            "resource_scope"
        ]
        cli_codes = [item["cli_code"] for item in resource_scope["cli_items"]]
        assert cli_codes == [
            "adev",
            "acli",
            "antcode",
            "linke",
            "linke-cli",
            "linkw-cli",
            "qmx-invoke-cli",
            "serverless",
            "derisk",
        ]

    @pytest.mark.asyncio
    async def test_merges_current_cli_items_before_default_cli_items(self):
        """Existing AgentPass CLI metadata wins, defaults fill only missing codes."""
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = [
            {"cli_code": "adev", "cli_name": "Custom Adev", "cli_desc": "kept"},
            {"cli_code": "custom-cli", "cli_name": "Custom", "cli_desc": None},
        ]
        bot_repository = MagicMock()
        bot_repository.get_by_id_and_owner.return_value = {
            "active_engine": "claude_code",
            "template_type": "personalCoding",
        }

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.test.1"}]),
            passport_update=passport_update,
            bot_repository=bot_repository,
        )

        result = await service.refresh_mcp_scope(
            user_id="user1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="claude_code",
        )

        assert result["success"] is True
        cli_items = passport_update.update_passport.call_args.kwargs["resource_scope"][
            "cli_items"
        ]
        cli_codes = [item["cli_code"] for item in cli_items]
        assert cli_items[0] == {
            "cli_code": "adev",
            "cli_name": "Custom Adev",
            "cli_desc": "kept",
        }
        assert "custom-cli" in cli_codes
        assert cli_codes.count("adev") == 1
        assert "antcode" in cli_codes

    @pytest.mark.asyncio
    async def test_bot_active_engine_wins_over_default_refresh_engine_for_cli_scope(
        self,
    ):
        """Background refresh defaults MCP routing but uses bot engine for CLI."""
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = []
        bot_repository = MagicMock()
        bot_repository.get_by_id_and_owner.return_value = {
            "active_engine": "aicoding",
            "template_type": "personalCoding",
        }

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.test.1"}]),
            passport_update=passport_update,
            bot_repository=bot_repository,
        )

        result = await service.refresh_mcp_scope(
            user_id="user1", entity_id="100", bot_id="bot1", entity_type="staff"
        )

        assert result["success"] is True
        resource_scope = passport_update.update_passport.call_args.kwargs[
            "resource_scope"
        ]
        cli_codes = [item["cli_code"] for item in resource_scope["cli_items"]]
        assert "antcode" in cli_codes
        assert len(cli_codes) == 9

    @pytest.mark.asyncio
    async def test_does_not_update_passport_when_bot_metadata_query_fails(self):
        """Bot metadata is required to safely preserve default CLI scope."""
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = []
        bot_repository = MagicMock()
        bot_repository.get_by_id_and_owner.side_effect = RuntimeError("db down")

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.test.1"}]),
            passport_update=passport_update,
            bot_repository=bot_repository,
        )

        result = await service.refresh_mcp_scope(
            user_id="user1",
            entity_id="100",
            bot_id="bot1",
            entity_type="staff",
            engine_type="openclaw",
        )

        assert result["success"] is False
        assert "获取 bot 信息失败" in result["error"]
        passport_update.query_passport_clis.assert_not_called()
        passport_update.update_passport.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_update_passport_when_cli_scope_query_fails(self):
        """updatePassport requires full MCP+CLI scope; missing CLI scope aborts the update."""
        passport_update = MagicMock()
        passport_update.query_passport_clis.side_effect = RuntimeError("tcauth down")
        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.test.1"}]),
            passport_update=passport_update,
        )

        result = await service.refresh_mcp_scope(
            user_id="user1",
            entity_id="100",
            bot_id="bot1",
            entity_type="staff",
            engine_type="openclaw",
        )

        assert result["success"] is False
        assert "查询 CLI 范围失败" in result["error"]
        passport_update.update_passport.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_error_when_scope_fails(self):
        """When the device rejects the scope declaration, result reflects
        failure and passport is NOT updated."""
        passport_update = MagicMock()
        plugin = _make_plugin(sync_all_mcp_servers=MagicMock(return_value=False))
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.test.1"}]),
            passport_update=passport_update,
            resolver=resolver,
            dispatcher=dispatcher,
        )

        result = await service.refresh_mcp_scope(
            user_id="user1",
            entity_id="100",
            bot_id="bot1",
            entity_type="staff",
            engine_type="openclaw",
        )

        assert result["success"] is False
        assert "白名单失败" in result["error"]
        passport_update.update_passport.assert_not_called()

    @pytest.mark.asyncio
    async def test_scope_returns_error_when_no_device(self):
        """No syncable device → the scope-declare leg surfaces the missing-conn
        error and passport is not updated."""
        passport_update = MagicMock()
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(unavailable=True)
        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.1"}]),
            passport_update=passport_update,
            resolver=resolver,
            dispatcher=dispatcher,
        )
        result = await service.refresh_mcp_scope(
            user_id="u1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="openclaw",
        )
        assert result["success"] is False
        assert "缺少设备连接信息" in result["error"]
        passport_update.update_passport.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_active_mcps_still_updates_passport(self):
        """Even with no active MCPs, scope_ok should clear passport."""
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = []
        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[]),
            passport_update=passport_update,
        )

        result = await service.refresh_mcp_scope(
            user_id="user1",
            entity_id="100",
            bot_id="bot1",
            entity_type="staff",
            engine_type="openclaw",
        )

        assert result["success"] is True
        passport_update.update_passport.assert_called_once()
        assert passport_update.update_passport.call_args.kwargs["resource_scope"] == {
            "mcp_codes": [],
            "cli_items": [],
        }

    @pytest.mark.asyncio
    async def test_scope_routes_through_resolver_with_entity_id(self):
        """The scope-declare leg resolves the plugin via resolver keyed on
        entity_id (compose/collect entity), and passport still updates."""
        passport_update = MagicMock()
        plugin = _make_plugin()
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.t.1"}]),
            passport_update=passport_update,
            resolver=resolver,
            dispatcher=dispatcher,
        )
        result = await service.refresh_mcp_scope(
            user_id="user1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="teclaw",
        )

        assert result["success"] is True
        resolver.resolve_for_bot.assert_called_once_with("bot1", "100")
        dispatcher.dispatch.assert_called_once()
        plugin.sync_all_mcp_servers.assert_called_once()
        passport_update.update_passport.assert_called_once()


class TestPushMcpConfigs:
    """Test sync_mcp_details: detail sync only, no passport update."""

    @pytest.mark.asyncio
    async def test_never_updates_passport(self):
        """sync_mcp_details should NEVER touch passport — that's refresh_mcp_scope's job."""
        passport_update = MagicMock()
        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(all_mcps=[{"server_code": "mcp.test.1"}]),
            passport_update=passport_update,
        )

        result = await service.sync_mcp_details(
            user_id="user1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="openclaw",
        )

        assert result["success"] is True
        passport_update.update_passport.assert_not_called()

    @pytest.mark.asyncio
    async def test_reports_failures(self):
        """When a per-MCP detail push fails, result reflects failures.

        Keyed on server_code (not call order) — the loop runs concurrently."""
        mcps = [
            {"server_code": "mcp.test.1", "name": "MCP 1"},
            {"server_code": "mcp.test.2", "name": "MCP 2"},
        ]

        def _push(mcp_data, **kwargs):
            return mcp_data.get("server_code") != "mcp.test.2"

        plugin = _make_plugin(sync_single_mcp=MagicMock(side_effect=_push))
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(all_mcps=mcps),
            resolver=resolver,
            dispatcher=dispatcher,
        )

        result = await service.sync_mcp_details(
            user_id="user1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="openclaw",
        )

        assert result["success"] is False
        assert result["success_count"] == 1
        assert result["failed_count"] == 1
        assert "mcp.test.1" in result["synced_server_codes"]

    @pytest.mark.asyncio
    async def test_bulk_routes_through_resolver_with_entity_id(self):
        """The bulk detail push resolves one plugin per bulk call (resolver keyed
        on entity_id) and pushes each MCP through it."""
        plugin = _make_plugin()
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(all_mcps=[{"server_code": "mcp.t.1"}]),
            resolver=resolver,
            dispatcher=dispatcher,
        )
        result = await service.sync_mcp_details(
            user_id="user1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="teclaw",
        )

        assert result["success"] is True
        resolver.resolve_for_bot.assert_called_once_with("bot1", "100")
        dispatcher.dispatch.assert_called_once()
        plugin.sync_single_mcp.assert_called_once()

    @pytest.mark.asyncio
    async def test_enriches_local_stdio_mcp_before_detail_push(self):
        """Bulk detail push preserves local stdio metadata from MCP lookup."""
        captured = {}

        def _push(mcp_data, **kwargs):
            captured.update(mcp_data)
            return True

        plugin = _make_plugin(sync_single_mcp=MagicMock(side_effect=_push))
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)
        mcp_center = MagicMock()
        mcp_center.get_mcp_list.return_value = {
            "success": True,
            "data": [
                {
                    "serverCode": "mcp.local.demo",
                    "server_code": "mcp.local.demo",
                    "name": "Local Demo",
                    "runMode": "LOCAL",
                    "stdioConfigs": [{"command": "node", "arguments": ["server.js"]}],
                }
            ],
        }

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(all_mcps=[{"server_code": "mcp.local.demo"}]),
            mcp_center=mcp_center,
            resolver=resolver,
            dispatcher=dispatcher,
        )

        result = await service.sync_mcp_details(
            user_id="user1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="openclaw",
        )

        assert result["success"] is True
        assert captured["runMode"] == "LOCAL"
        assert captured["stdioConfigs"][0]["command"] == "node"

    @pytest.mark.asyncio
    async def test_offline_device_raises_to_error(self):
        """No syncable device → sync_mcp_details returns a missing-connection error."""
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(unavailable=True)
        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(all_mcps=[{"server_code": "mcp.t.1"}]),
            resolver=resolver,
            dispatcher=dispatcher,
        )
        result = await service.sync_mcp_details(
            user_id="user1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="openclaw",
        )
        assert result["success"] is False
        assert "缺少设备连接信息" in result["error"]


class TestPushMcpConfig:
    """Test sync_mcp_detail (single MCP to single bot)."""

    @pytest.mark.asyncio
    async def test_success(self):
        service = _make_sync_service()
        result = await service.sync_mcp_detail(
            user_id="u1", mcp_data={"server_code": "mcp.test.1"}, bot_id="bot1",
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_offline_device_returns_error(self):
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(unavailable=True)
        service = _make_sync_service(resolver=resolver, dispatcher=dispatcher)
        result = await service.sync_mcp_detail(
            user_id="u1", mcp_data={"server_code": "mcp.test.1"}, bot_id="bot1",
        )
        assert result["success"] is False
        assert "缺少设备连接信息" in result["error"]

    @pytest.mark.asyncio
    async def test_routes_through_resolver(self):
        """Single add resolves the plugin via resolver keyed on user_id and pushes
        the MCP through it (the plugin decides per-container delivery)."""
        plugin = _make_plugin()
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)

        service = _make_sync_service(resolver=resolver, dispatcher=dispatcher)
        result = await service.sync_mcp_detail(
            user_id="u1", mcp_data={"server_code": "mcp.x"}, bot_id="bot1",
        )

        assert result["success"] is True
        resolver.resolve_for_bot.assert_called_once_with("bot1", "u1")
        dispatcher.dispatch.assert_called_once()
        plugin.sync_single_mcp.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolves_plugin_by_entity_id_when_given(self):
        """When entity_id is supplied (shared/org bot), resolver receives
        entity_id — so the teclaw whole-artifact compose targets the bot owner's
        entity, not the (possibly different) caller user_id. Consistent with the
        remove/scope/bulk/batch paths."""
        plugin = _make_plugin()
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)

        service = _make_sync_service(resolver=resolver, dispatcher=dispatcher)
        result = await service.sync_mcp_detail(
            user_id="viewer", entity_id="owner_entity",
            mcp_data={"server_code": "mcp.x"}, bot_id="bot1",
        )

        assert result["success"] is True
        resolver.resolve_for_bot.assert_called_once_with("bot1", "owner_entity")
        # user_id (not entity_id) still drives the per-user payload merge.
        assert service.mcp_config_service.build_mcp_sync_payload.call_args.kwargs["user_id"] == "viewer"

    @pytest.mark.asyncio
    async def test_delivery_failure_surfaces_error(self):
        """A failed device push surfaces success=False so add_mcp_to_skill_set
        rolls back."""
        plugin = _make_plugin(sync_single_mcp=MagicMock(return_value=False))
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)
        service = _make_sync_service(resolver=resolver, dispatcher=dispatcher)
        result = await service.sync_mcp_detail(
            user_id="u1", mcp_data={"server_code": "mcp.x"}, bot_id="bot1",
        )

        assert result["success"] is False
        assert "推送 MCP 配置失败" in result["error"]


class TestRemoveMcpConfig:
    """Test remove_mcp_detail (single MCP removal from bot)."""

    @pytest.mark.asyncio
    async def test_success(self):
        service = _make_sync_service()
        result = await service.remove_mcp_detail(
            server_code="mcp.test.1", bot_id="bot1", user_id="u1",
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_offline_device_returns_error(self):
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(unavailable=True)
        service = _make_sync_service(resolver=resolver, dispatcher=dispatcher)
        result = await service.remove_mcp_detail(
            server_code="mcp.test.1", bot_id="bot1", user_id="u1",
        )
        assert result["success"] is False
        assert "缺少设备连接信息" in result["error"]

    @pytest.mark.asyncio
    async def test_routes_through_resolver(self):
        """Remove resolves the plugin via resolver(bot_id, user_id) and calls
        sync_remove_mcp on it."""
        plugin = _make_plugin()
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)

        service = _make_sync_service(resolver=resolver, dispatcher=dispatcher)
        result = await service.remove_mcp_detail(
            server_code="mcp.x", bot_id="bot1", user_id="u1",
        )

        assert result["success"] is True
        resolver.resolve_for_bot.assert_called_once_with("bot1", "u1")
        dispatcher.dispatch.assert_called_once()
        plugin.sync_remove_mcp.assert_called_once_with("mcp.x")


class TestSyncMcpDetailToAllBots:
    """Multi-bot batch: uniform probe→push, provider-blind, Option B rollback."""

    def _service_with_one_bot(self, *, resolver, dispatcher):
        bot_repo = MagicMock()
        bot_repo.list_by_entity.return_value = (1, [{"bot_id": "bot1"}])
        return _make_sync_service(
            bot_repository=bot_repo,
            resolver=resolver,
            dispatcher=dispatcher,
        )

    @pytest.mark.asyncio
    async def test_routes_each_bot_through_resolver(self):
        """Each bot is resolved via resolver(bot_id, entity_id), probed, pushed."""
        plugin = _make_plugin()
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)
        service = self._service_with_one_bot(resolver=resolver, dispatcher=dispatcher)

        result = await service.sync_mcp_detail_to_all_bots(
            user_id="u1", server_code="mcp.x", mcp_data={"server_code": "mcp.x"},
            entity_id="100", entity_type="staff",
        )

        assert result["success"] is True
        assert result["sync_results"][0]["bot_id"] == "bot1"
        assert result["sync_results"][0]["synced"] is True
        resolver.resolve_for_bot.assert_called_once_with("bot1", "100")
        dispatcher.dispatch.assert_called_once()
        plugin.has_mcp.assert_called_once_with("mcp.x")
        plugin.sync_single_mcp.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_devices_fail_returns_failure(self):
        """When every device that has the MCP fails to sync, the batch reports
        failure (→ caller rolls back). Uniform across providers (Option B)."""
        plugin = _make_plugin(sync_single_mcp=MagicMock(return_value=False))
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)
        service = self._service_with_one_bot(resolver=resolver, dispatcher=dispatcher)

        result = await service.sync_mcp_detail_to_all_bots(
            user_id="u1", server_code="mcp.x", mcp_data={"server_code": "mcp.x"},
            entity_id="100", entity_type="staff",
        )

        assert result["success"] is False
        assert result["sync_results"][0]["synced"] is False

    @pytest.mark.asyncio
    async def test_device_without_mcp_is_skipped(self):
        """A device that doesn't report the MCP is skipped and does not count
        toward the rollback gate (success stays True)."""
        plugin = _make_plugin(has_mcp=MagicMock(return_value=False))
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)
        service = self._service_with_one_bot(resolver=resolver, dispatcher=dispatcher)

        result = await service.sync_mcp_detail_to_all_bots(
            user_id="u1", server_code="mcp.x", mcp_data={"server_code": "mcp.x"},
            entity_id="100", entity_type="staff",
        )

        assert result["success"] is True
        assert result["sync_results"][0]["synced"] is False
        plugin.sync_single_mcp.assert_not_called()

    @pytest.mark.asyncio
    async def test_bot_without_device_is_skipped(self):
        """A bot with no syncable device is skipped (best-effort) and does not
        fail the batch."""
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(unavailable=True)
        service = self._service_with_one_bot(resolver=resolver, dispatcher=dispatcher)

        result = await service.sync_mcp_detail_to_all_bots(
            user_id="u1", server_code="mcp.x", mcp_data={"server_code": "mcp.x"},
            entity_id="100", entity_type="staff",
        )

        assert result["success"] is True
        assert result["sync_results"][0]["reason"] == "缺少设备连接信息"
