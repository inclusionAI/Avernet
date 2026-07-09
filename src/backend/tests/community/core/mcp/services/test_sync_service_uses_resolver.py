"""Contract tests — MCPSyncService 5 caller 走 resolver + dispatcher.

Task 2 of `docs/superpowers/plans/2026-06-15-device-sync-supplier-for-bot-cleanup.md`:
确认 MCPSyncService 下 device_sync 的取得都通过
``DeviceContextResolver.resolve_for_bot(bot_id, user_id) → dispatcher.dispatch(ctx)``,
不再走旧的 ``device_sync_supplier.for_bot(bot_id, user_id, engine_type=?)`` 闭包模式。

5+1 个 case 覆盖:
- L211 sync_mcp_detail (single MCP push)
- L256 remove_mcp_detail (single MCP removal)
- L388 sync_mcp_detail_to_all_bots (multi-bot batch)
- L487 _declare_mcp_scope (whitelist declaration, via refresh_mcp_scope)
- L547 _sync_mcp_details (bulk detail push, via sync_mcp_details)
- ctor guard — MCPSyncService.__init__ 不再含 ``device_sync_supplier``
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.device_context import DeviceContext


def _make_ctx(bot_id: str = "bot-1", user_id: str = "u-1") -> DeviceContext:
    """工厂:生成稳定的 DeviceContext mock。"""
    return DeviceContext(
        provider="baas",
        conn_info={"engine_type": "openclaw"},
        binding_id=42,
        bot_id=bot_id,
        user_id=user_id,
    )


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


def _make_mcp_provider(mcps=None, all_mcps=None):
    """Create a mock BotMCPProvider."""
    provider = MagicMock()
    provider.collect_bot_active_mcps.return_value = mcps or []
    provider.collect_bot_mcps.return_value = all_mcps or mcps or []
    provider.get_active_skill_sets_mcp_summary.return_value = ([], [])
    return provider


def _make_service(
    *,
    resolver=None,
    dispatcher=None,
    plugin=None,
    mcp_provider=None,
    bot_repository=None,
    passport_update=None,
):
    """Construct MCPSyncService bypassing __init__, wiring resolver + dispatcher."""
    from agentclaw.community.core.mcp.services.sync_service import MCPSyncService

    service = MCPSyncService.__new__(MCPSyncService)
    provider = mcp_provider or _make_mcp_provider()
    service._mcp_provider_factory = lambda: provider
    service._mcp_provider_cached = provider
    service.mcp_center = MagicMock()
    service.user_mcp_config_repo = MagicMock()
    service.passport_update = passport_update or MagicMock()
    service.mcp_config_service = MagicMock()
    service.mcp_config_service.build_mcp_sync_payload.return_value = (
        None, {}, "PROD", None
    )
    service.bot_repository = bot_repository or MagicMock()

    # New resolver/dispatcher wiring — provider thunks (Task 1 style)
    actual_resolver = resolver or MagicMock()
    actual_dispatcher = dispatcher or MagicMock()
    if resolver is None:
        actual_resolver.resolve_for_bot.return_value = _make_ctx()
    if dispatcher is None:
        actual_dispatcher.dispatch.return_value = (plugin or _make_plugin())
    service._resolver_provider = lambda: actual_resolver
    service._device_sync_dispatcher_provider = lambda: actual_dispatcher
    return service


# ── ctor 守护 ─────────────────────────────────────────────────────────


class TestMCPSyncServiceCtor:
    """MCPSyncService.__init__ 不再含 device_sync_supplier,改注入 resolver + dispatcher。"""

    def test_ctor_signature_replaces_supplier_with_resolver_and_dispatcher(self):
        from agentclaw.community.core.mcp.services.sync_service import MCPSyncService

        sig = inspect.signature(MCPSyncService.__init__)
        params = set(sig.parameters.keys())
        assert "device_sync_supplier" not in params, (
            "MCPSyncService.__init__ 不应再注入 device_sync_supplier"
        )
        # Lazy thunks(Callable[[], ...])打破构造期 DI 循环 —— 与
        # ``SkillSetServiceFactory`` 同款手法。详见 ctor docstring。
        assert "resolver_provider" in params, (
            "MCPSyncService.__init__ 应注入 ``resolver_provider`` (lazy thunk "
            "返回 DeviceContextResolver,打破 DI 循环)"
        )
        assert "device_sync_dispatcher_provider" in params, (
            "MCPSyncService.__init__ 应注入 ``device_sync_dispatcher_provider`` "
            "(lazy thunk 返回 DeviceSyncDispatcher)"
        )


# ── L211 sync_mcp_detail ──────────────────────────────────────────────


class TestSyncMcpDetailUsesResolver:
    """L211 sync_mcp_detail 走 resolver(bot_id, entity_id or user_id) + dispatcher。"""

    @pytest.mark.asyncio
    async def test_resolves_via_resolver_and_dispatches_with_user_id(self):
        ctx = _make_ctx()
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx
        plugin = _make_plugin()
        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = plugin

        service = _make_service(resolver=resolver, dispatcher=dispatcher)

        result = await service.sync_mcp_detail(
            user_id="u1", mcp_data={"server_code": "mcp.x"}, bot_id="bot1",
        )

        assert result["success"] is True
        # entity_id 未提供 → 落到 user_id
        resolver.resolve_for_bot.assert_called_once_with("bot1", "u1")
        dispatcher.dispatch.assert_called_once_with(ctx)
        plugin.sync_single_mcp.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolves_with_entity_id_when_supplied(self):
        """When entity_id is supplied (shared/org bot), resolver gets entity_id."""
        ctx = _make_ctx()
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx
        plugin = _make_plugin()
        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = plugin

        service = _make_service(resolver=resolver, dispatcher=dispatcher)

        result = await service.sync_mcp_detail(
            user_id="viewer",
            entity_id="owner_entity",
            mcp_data={"server_code": "mcp.x"},
            bot_id="bot1",
        )

        assert result["success"] is True
        resolver.resolve_for_bot.assert_called_once_with("bot1", "owner_entity")
        dispatcher.dispatch.assert_called_once_with(ctx)


# ── L256 remove_mcp_detail ────────────────────────────────────────────


class TestRemoveMcpDetailUsesResolver:
    """L256 remove_mcp_detail 走 resolver(bot_id, user_id) + dispatcher。"""

    @pytest.mark.asyncio
    async def test_resolves_via_resolver_and_dispatches(self):
        ctx = _make_ctx()
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx
        plugin = _make_plugin()
        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = plugin

        service = _make_service(resolver=resolver, dispatcher=dispatcher)

        result = await service.remove_mcp_detail(
            server_code="mcp.x", bot_id="bot1", user_id="u1",
        )

        assert result["success"] is True
        resolver.resolve_for_bot.assert_called_once_with("bot1", "u1")
        dispatcher.dispatch.assert_called_once_with(ctx)
        plugin.sync_remove_mcp.assert_called_once_with("mcp.x")


# ── L388 sync_mcp_detail_to_all_bots ──────────────────────────────────


class TestSyncMcpDetailToAllBotsUsesResolver:
    """L388 sync_mcp_detail_to_all_bots 走 resolver(bot_id, entity_id) + dispatcher。"""

    @pytest.mark.asyncio
    async def test_resolves_each_bot_via_resolver(self):
        ctx = _make_ctx()
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx
        plugin = _make_plugin()
        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = plugin

        bot_repo = MagicMock()
        bot_repo.list_by_entity.return_value = (1, [{"bot_id": "bot1"}])

        service = _make_service(
            resolver=resolver,
            dispatcher=dispatcher,
            bot_repository=bot_repo,
        )

        result = await service.sync_mcp_detail_to_all_bots(
            user_id="u1",
            server_code="mcp.x",
            mcp_data={"server_code": "mcp.x"},
            entity_id="100",
            entity_type="staff",
        )

        assert result["success"] is True
        # resolver 用 entity_id(与 compose/collect entity 口径一致)
        resolver.resolve_for_bot.assert_called_once_with("bot1", "100")
        dispatcher.dispatch.assert_called_once_with(ctx)
        plugin.has_mcp.assert_called_once_with("mcp.x")
        plugin.sync_single_mcp.assert_called_once()


# ── L487 _declare_mcp_scope (via refresh_mcp_scope) ───────────────────


class TestDeclareMcpScopeUsesResolver:
    """L487 _declare_mcp_scope 走 resolver(bot_id, entity_id) + dispatcher。

    通过 refresh_mcp_scope 入口触发(_declare_mcp_scope 是内部辅助)。
    """

    @pytest.mark.asyncio
    async def test_scope_resolves_via_resolver_and_dispatches(self):
        ctx = _make_ctx()
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx
        plugin = _make_plugin()
        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = plugin

        passport_update = MagicMock()
        service = _make_service(
            resolver=resolver,
            dispatcher=dispatcher,
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.t.1"}]),
            passport_update=passport_update,
        )

        result = await service.refresh_mcp_scope(
            user_id="u1",
            entity_id="100",
            bot_id="bot1",
            entity_type="staff",
            engine_type="teclaw",
        )

        assert result["success"] is True
        # resolver 用 entity_id
        resolver.resolve_for_bot.assert_called_once_with("bot1", "100")
        dispatcher.dispatch.assert_called_once_with(ctx)
        plugin.sync_all_mcp_servers.assert_called_once()
        passport_update.update_passport.assert_called_once()


# ── L547 _sync_mcp_details (via sync_mcp_details) ─────────────────────


class TestSyncMcpDetailsBulkUsesResolver:
    """L547 _sync_mcp_details 走 resolver(bot_id, entity_id) + dispatcher。

    通过 sync_mcp_details 入口触发(_sync_mcp_details 是内部辅助)。
    """

    @pytest.mark.asyncio
    async def test_bulk_resolves_via_resolver_and_dispatches(self):
        ctx = _make_ctx()
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx
        plugin = _make_plugin()
        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = plugin

        service = _make_service(
            resolver=resolver,
            dispatcher=dispatcher,
            mcp_provider=_make_mcp_provider(all_mcps=[{"server_code": "mcp.t.1"}]),
        )

        result = await service.sync_mcp_details(
            user_id="u1",
            entity_id="100",
            bot_id="bot1",
            entity_type="staff",
            engine_type="teclaw",
        )

        assert result["success"] is True
        # resolver 用 entity_id(与 collect_bot_*mcps 的 entity 口径一致)
        resolver.resolve_for_bot.assert_called_once_with("bot1", "100")
        dispatcher.dispatch.assert_called_once_with(ctx)
        plugin.sync_single_mcp.assert_called_once()


# ── 错误兼容:resolver 抛 DeviceNotBoundError → wire shape 保持 ─────────


class TestResolverErrorsMapToMissingDevice:
    """resolver 抛 DeviceNotBoundError / UnknownProviderError 时,
    sync_mcp_detail / remove_mcp_detail / _declare_mcp_scope / _sync_mcp_details
    都映射到旧 ``{"success": False, "error": "缺少设备连接信息..."}`` 的 wire shape。
    sync_mcp_detail_to_all_bots 走 best-effort 跳过路径。
    """

    @pytest.mark.asyncio
    async def test_sync_mcp_detail_when_not_bound(self):
        from agentclaw.community.core.devices.services.device_context import (
            DeviceNotBoundError,
        )

        resolver = MagicMock()
        resolver.resolve_for_bot.side_effect = DeviceNotBoundError("no binding")
        dispatcher = MagicMock()

        service = _make_service(resolver=resolver, dispatcher=dispatcher)
        result = await service.sync_mcp_detail(
            user_id="u1", mcp_data={"server_code": "mcp.x"}, bot_id="bot1",
        )
        assert result["success"] is False
        assert "缺少设备连接信息" in result["error"]
        dispatcher.dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_remove_mcp_detail_when_not_bound(self):
        from agentclaw.community.core.devices.services.device_context import (
            DeviceNotBoundError,
        )

        resolver = MagicMock()
        resolver.resolve_for_bot.side_effect = DeviceNotBoundError("no binding")
        dispatcher = MagicMock()

        service = _make_service(resolver=resolver, dispatcher=dispatcher)
        result = await service.remove_mcp_detail(
            server_code="mcp.x", bot_id="bot1", user_id="u1",
        )
        assert result["success"] is False
        assert "缺少设备连接信息" in result["error"]
        dispatcher.dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_mcp_detail_to_all_bots_skips_when_not_bound(self):
        from agentclaw.community.core.devices.services.device_context import (
            DeviceNotBoundError,
        )

        resolver = MagicMock()
        resolver.resolve_for_bot.side_effect = DeviceNotBoundError("no binding")
        dispatcher = MagicMock()

        bot_repo = MagicMock()
        bot_repo.list_by_entity.return_value = (1, [{"bot_id": "bot1"}])

        service = _make_service(
            resolver=resolver,
            dispatcher=dispatcher,
            bot_repository=bot_repo,
        )
        result = await service.sync_mcp_detail_to_all_bots(
            user_id="u1",
            server_code="mcp.x",
            mcp_data={"server_code": "mcp.x"},
            entity_id="100",
            entity_type="staff",
        )
        # batch 走 best-effort: success=True,但单 bot 标记 reason
        assert result["success"] is True
        assert result["sync_results"][0]["reason"] == "缺少设备连接信息"
        dispatcher.dispatch.assert_not_called()
