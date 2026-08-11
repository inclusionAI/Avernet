"""Contract tests — skill_center 10 caller 走 resolver + dispatcher.

Task 2.1 of `docs/superpowers/plans/2026-06-15-device-sync-supplier-for-bot-cleanup.md`:
确认 skill_center 模块下 device_sync / device_fs 的取得都通过
``DeviceContextResolver.resolve_for_bot(bot_id, user_id) → dispatcher.dispatch(ctx)``,
不再走旧的 ``supplier.for_bot(bot_id, user_id, owner_id?, engine_type=?)`` 闭包模式。

6 个 case 覆盖:
- SkillSetService 2 个(L266 _sync_symlinks_to_device_if_needed,
  L1773 _DeviceSyncMixin._do_device_sync 通过 SkillSetSwitcher 触发)
- SkillSetSwitcher 1 个(L1966 _cleanup_all_non_reserved_items — Pattern B 用 device_fs)
- SkillPropagationService 1 个(L273 _refresh_bot)
- SkillSymlinkListener 1 个(L109 handle)
- SkillParameterServiceFactory 1 个(factories.py L223 create — Pattern B inline)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentclaw.community.core.devices.services.device_context import DeviceContext


def _make_ctx(bot_id: str = "bot-1", user_id: str = "user-1") -> DeviceContext:
    """工厂:生成稳定的 DeviceContext mock。"""
    return DeviceContext(
        provider="baas",
        conn_info={"engine_type": "openclaw"},
        binding_id=42,
        bot_id=bot_id,
        user_id=user_id,
    )


# ── SkillSetService ──────────────────────────────────────────────────


class TestSkillSetServiceUsesResolver:
    """SkillSetService._sync_symlinks_to_device_if_needed 走 resolver + dispatcher。"""

    def test_sync_symlinks_resolves_via_resolver_and_dispatches(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import (
            SkillSetService,
        )

        ctx = _make_ctx()
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx

        sync_plugin = MagicMock()
        sync_plugin.sync_symlinks.return_value = {"success": True, "message": "ok"}
        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = sync_plugin

        with patch(
            "agentclaw.community.core.skill_center.services.skill_set_service."
            "WorkspacePathFactory"
        ):
            svc = SkillSetService(
                skill_repo=MagicMock(),
                skill_set_repo=MagicMock(),
                mcp_center=MagicMock(),
                mcp_config_service=MagicMock(),
                skill_service=MagicMock(),
                bot_repo=MagicMock(),
                bot_id="bot-1",
                resolver=resolver,
                device_sync_dispatcher=dispatcher,
                path_factory=MagicMock(),
            )

        # 准备空 mappings(本测试只关心 resolver+dispatch 的调用契约)
        svc.get_symlink_mappings = MagicMock(return_value=[])

        ok = svc._sync_symlinks_to_device_if_needed(user_id="user-1")

        assert ok is True
        resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
        dispatcher.dispatch.assert_called_once_with(ctx)
        sync_plugin.sync_symlinks.assert_called_once_with([])

    def test_sync_runtime_keeps_owner_scope_when_paths_use_bot_entity(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import (
            SkillSetService,
        )

        ctx = _make_ctx()
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx
        dispatcher = MagicMock()
        sync_plugin = MagicMock()
        sync_plugin.sync_symlinks.return_value = {"success": True, "message": "ok"}
        dispatcher.dispatch.return_value = sync_plugin
        bot_repo = MagicMock()

        with patch(
            "agentclaw.community.core.skill_center.services.skill_set_service."
            "WorkspacePathFactory"
        ):
            svc = SkillSetService(
                skill_repo=MagicMock(),
                skill_set_repo=MagicMock(),
                mcp_center=MagicMock(),
                mcp_config_service=MagicMock(),
                skill_service=MagicMock(),
                bot_repo=bot_repo,
                user_id="owner-1",
                entity_id="project-1",
                entity_type="project",
                bot_id="bot-1",
                resolver=resolver,
                device_sync_dispatcher=dispatcher,
                path_factory=MagicMock(),
            )

        svc.get_symlink_mappings = MagicMock(return_value=[])

        assert svc.sync_runtime() is True
        svc.get_symlink_mappings.assert_called_once_with(
            user_id="owner-1", bolt_id="bot-1"
        )
        resolver.resolve_for_bot.assert_called_once_with("bot-1", "owner-1")
        bot_repo.get_by_id_and_owner.assert_called_once_with("bot-1", "owner-1")


# ── _DeviceSyncMixin via SkillSetSwitcher ────────────────────────────


class TestDeviceSyncMixinUsesResolver:
    """L1773 _DeviceSyncMixin._do_device_sync 走 resolver + dispatcher (通过
    SkillSetSwitcher 间接触发,因为 _DeviceSyncMixin 是 mixin)。"""

    def test_do_device_sync_resolves_and_dispatches(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import (
            SkillSetSwitcher,
        )

        ctx = _make_ctx()
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx

        sync_plugin = MagicMock()
        sync_plugin.sync_symlinks.return_value = {"success": True, "message": "ok"}
        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = sync_plugin

        skill_set_factory = MagicMock()
        fake_inner = MagicMock()
        fake_inner.bot_id = "bot-1"
        fake_inner.engine_type = "openclaw"
        fake_inner.get_symlink_mappings.return_value = []
        skill_set_factory.create.return_value = fake_inner

        switcher = SkillSetSwitcher(
            skill_set_factory=skill_set_factory,
            resolver=resolver,
            device_sync_dispatcher=dispatcher,
            device_plugin=MagicMock(),
            path_factory=MagicMock(),
            device_fs_dispatcher=MagicMock(),
            edit_guard=MagicMock(),
            user_id="user-1",
            entity_id="user-1",
            bot_id="bot-1",
            engine_type="openclaw",
        )

        result = switcher._do_device_sync(user_id="user-1", caller="test")

        assert result["success"] is True
        resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
        dispatcher.dispatch.assert_called_once_with(ctx)
        sync_plugin.sync_symlinks.assert_called_once_with([])


# ── SkillSetSwitcher._cleanup_all_non_reserved_items (Pattern B) ─────


class TestSwitcherCleanupUsesResolver:
    """L1966 SkillSetSwitcher._cleanup_all_non_reserved_items 改造 device_fs
    走 resolver + dispatcher。"""

    def test_cleanup_uses_resolver_and_dispatcher(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import (
            SkillSetSwitcher,
        )

        ctx = _make_ctx()
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx

        async def _async_list_dir(path):
            return []

        async def _async_delete_tree(path):
            return True

        device_fs = MagicMock()
        device_fs.list_dir = MagicMock(side_effect=_async_list_dir)
        device_fs.delete_tree = MagicMock(side_effect=_async_delete_tree)
        device_fs_dispatcher = MagicMock()
        device_fs_dispatcher.dispatch.return_value = device_fs

        skill_set_factory = MagicMock()
        fake_inner = MagicMock()
        fake_inner.bot_id = "bot-1"
        fake_inner.entity_id = "user-1"
        fake_inner.skill_service.RESERVED_SKILL_NAMES = (
            "skills-repo",
            "skills-local",
            ".current_skill_set",
            "skill_sets.json",
        )
        skill_set_factory.create.return_value = fake_inner

        switcher = SkillSetSwitcher(
            skill_set_factory=skill_set_factory,
            resolver=resolver,
            device_sync_dispatcher=MagicMock(),
            device_plugin=MagicMock(),
            path_factory=MagicMock(),
            device_fs_dispatcher=device_fs_dispatcher,
            edit_guard=MagicMock(),
            user_id="user-1",
            entity_id="user-1",
            bot_id="bot-1",
            engine_type="openclaw",
        )

        result = switcher._cleanup_all_non_reserved_items()

        assert result == []
        resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
        device_fs_dispatcher.dispatch.assert_called_once_with(ctx)


# ── SkillPropagationService ──────────────────────────────────────────


class TestSkillPropagationServiceUsesResolver:
    """L273 SkillPropagationService._refresh_bot 走 resolver + dispatcher。"""

    def test_refresh_bot_resolves_and_dispatches(self):
        from agentclaw.community.core.skill_center.services.skill_propagation_service import SkillPropagationService

        ctx = _make_ctx()
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx

        sync_plugin = MagicMock()
        sync_plugin.sync_symlinks.return_value = {"success": True}
        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = sync_plugin

        skill_set_factory = MagicMock()
        fake_svc = MagicMock()
        fake_svc.get_symlink_mappings.return_value = []
        skill_set_factory.create.return_value = fake_svc

        svc = SkillPropagationService(
            log_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            resolver=resolver,
            device_sync_dispatcher=dispatcher,
            skill_set_service_factory=skill_set_factory,
            sync_service=None,
        )

        ok = svc._refresh_bot(
            bot_id="bot-1",
            owner_id="user-1",
            skill_uuid="sku-x",
            engine_type="openclaw",
            env="prod",
        )

        assert ok is True
        resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
        dispatcher.dispatch.assert_called_once_with(ctx)
        sync_plugin.sync_symlinks.assert_called_once_with([])


# ── SkillSymlinkListener ─────────────────────────────────────────────


class TestSkillSymlinkListenerUsesResolver:
    """L109 SkillSymlinkListener.handle 走 resolver + dispatcher。"""

    def test_handle_resolves_and_dispatches(self):
        from agentclaw.community.core.events.types import DeviceActivatedEvent
        from agentclaw.community.core.skill_center.services.skill_symlink_listener import (
            SkillSymlinkListener,
        )

        ctx = _make_ctx()
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx

        sync_plugin = MagicMock()
        sync_plugin.sync_symlinks.return_value = {"success": True, "message": "ok"}
        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = sync_plugin

        bot_repo = MagicMock()
        bot_repo.get_by_binding_id.return_value = {
            "bot_id": "bot-1",
            "owner_id": "user-1",
            "active_engine": "openclaw",
        }

        skill_set_factory = MagicMock()
        fake_svc = MagicMock()
        fake_svc.get_symlink_mappings.return_value = []
        skill_set_factory.create.return_value = fake_svc

        listener = SkillSymlinkListener(
            bot_repo=bot_repo,
            skill_set_factory=skill_set_factory,
            resolver=resolver,
            device_sync_dispatcher=dispatcher,
        )

        event = DeviceActivatedEvent(
            device_id="x",
            binding_id=42,
            entity_id="user-1",
            entity_type="staff",
            device_provider="baas",
            sandbox_id="sbx-1",
        )
        listener.handle(event)

        resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
        dispatcher.dispatch.assert_called_once_with(ctx)


# ── SkillParameterServiceFactory ─────────────────────────────────────


class TestSkillParameterServiceFactoryUsesResolver:
    """factories.py L223 SkillParameterServiceFactory.create 走 resolver +
    device_fs_dispatcher.dispatch(ctx) (Pattern B inline)。"""

    def test_create_resolves_and_dispatches_for_device_fs(self):
        import asyncio

        from agentclaw.community.core.skill_center.factories import (
            SkillParameterServiceFactory,
        )

        ctx = _make_ctx()
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx

        device_fs = MagicMock()
        device_fs_dispatcher = MagicMock()
        device_fs_dispatcher.dispatch.return_value = device_fs

        factory = SkillParameterServiceFactory(
            resolver=resolver,
            device_fs_dispatcher=device_fs_dispatcher,
        )

        # SkillParameterService.async_load is async — disable load_on_init
        # so we don't need to mock filesystem IO.
        service = asyncio.run(
            factory.create(bot_id="bot-1", user_id="user-1", load_on_init=False)
        )

        assert service is not None
        resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
        device_fs_dispatcher.dispatch.assert_called_once_with(ctx)
