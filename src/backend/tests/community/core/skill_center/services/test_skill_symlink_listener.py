"""Tests for agentclaw.community.core.skill_center.services.skill_symlink_listener."""

from __future__ import annotations

from unittest.mock import MagicMock, call

from agentclaw.community.core.events.types import DeviceActivatedEvent
from agentclaw.community.core.skill_center.services.skill_symlink_listener import (
    SkillSymlinkListener,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
    runtime_uses_pool_paths,
)


def _make_event(**overrides) -> DeviceActivatedEvent:
    defaults = dict(
        device_id="staff_u001_default",
        binding_id=42,
        entity_id="u001",
        entity_type="staff",
        device_provider="arca",
        sandbox_id="sbx-abc@alt-0",
    )
    defaults.update(overrides)
    return DeviceActivatedEvent(**defaults)


def _make_listener(
    skill_set_service=None,
    sync_plugin=None,
    bot_query=None,
    layout_repository=None,
    skills_pool_wakeup=None,
):
    factory = MagicMock()
    if skill_set_service is not None:
        factory.create.return_value = skill_set_service

    resolver = MagicMock()
    fake_ctx = MagicMock()
    fake_ctx.binding_id = 42
    resolver.resolve_for_bot.return_value = fake_ctx

    dispatcher = MagicMock()
    if sync_plugin is not None:
        dispatcher.dispatch.return_value = sync_plugin

    bot_repo = bot_query if bot_query is not None else MagicMock()
    desktop_layout_authority = None
    if layout_repository is not None:

        def desktop_layout_authority(bot):
            state = layout_repository.get(bot)
            if state.phase is SkillLayoutPhase.POOL_ACTIVE:
                return "pool"
            if runtime_uses_pool_paths(state):
                return "transition"
            return "legacy"

    listener = SkillSymlinkListener(
        bot_repo=bot_repo,
        skill_set_factory=factory,
        resolver=resolver,
        device_sync_dispatcher=dispatcher,
        desktop_layout_authority=desktop_layout_authority,
        desktop_reconcile_wakeup=(
            skills_pool_wakeup.handle
            if skills_pool_wakeup is not None
            else None
        ),
    )
    return listener, factory, dispatcher, resolver


def _layout_state(
    *,
    layout: SkillLayout = SkillLayout.LEGACY,
    phase: SkillLayoutPhase = SkillLayoutPhase.LEGACY_ACTIVE,
) -> BotSkillLayoutState:
    return BotSkillLayoutState(
        scope=BotSkillLayoutScope(
            env="pre",
            entity_id="entity-1",
            bot_id="desktop-1",
        ),
        active_layout=layout,
        target_layout=SkillLayout.POOL if layout is SkillLayout.LEGACY else None,
        phase=phase,
        migration_generation="G1" if phase is not SkillLayoutPhase.LEGACY_ACTIVE else None,
        persisted=phase is not SkillLayoutPhase.LEGACY_ACTIVE,
    )


class TestHandleDeviceActivated:
    def test_happy_path_syncs_symlinks(self):
        event = _make_event()

        fake_bot = {"bot_id": "default", "owner_id": "u001"}
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = fake_bot

        fake_sync_plugin = MagicMock()
        fake_sync_plugin.sync_symlinks.return_value = {"success": True, "message": "ok"}

        symlinks = [MagicMock(to_dict=lambda: {"source": "/a", "target": "/b"})]
        fake_service = MagicMock()
        fake_service.get_symlink_mappings.return_value = symlinks

        listener, _, dispatcher, resolver = _make_listener(fake_service, fake_sync_plugin, bot_query=bot_query)
        listener.handle(event)

        bot_query.get_by_binding_id.assert_called_once_with(42)
        resolver.resolve_for_bot.assert_called_once_with("default", "u001")
        dispatcher.dispatch.assert_called_once()
        fake_service.get_symlink_mappings.assert_called_once_with(
            user_id="u001", bolt_id="default"
        )
        fake_sync_plugin.sync_symlinks.assert_called_once_with(
            [{"source": "/a", "target": "/b"}]
        )

    def test_skips_when_bot_not_found(self):
        event = _make_event()
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = None

        listener, _, dispatcher, resolver = _make_listener(bot_query=bot_query)
        listener.handle(event)

        resolver.resolve_for_bot.assert_not_called()
        dispatcher.dispatch.assert_not_called()

    def test_skips_when_event_binding_is_no_longer_current(self):
        event = _make_event(binding_id=42)
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {
            "bot_id": "default",
            "owner_id": "u001",
        }
        sync_plugin = MagicMock()
        service = MagicMock()
        listener, factory, dispatcher, resolver = _make_listener(
            service,
            sync_plugin,
            bot_query=bot_query,
        )
        resolver.resolve_for_bot.return_value.binding_id = 84

        listener.handle(event)

        resolver.resolve_for_bot.assert_called_once_with("default", "u001")
        factory.create.assert_not_called()
        dispatcher.dispatch.assert_not_called()
        sync_plugin.sync_symlinks.assert_not_called()

    def test_published_service_binding_never_uses_draft_db_mapping(self):
        event = _make_event(binding_id=42, device_provider="baas")
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {
            "bot_id": "service-bot-1",
            "owner_id": "u001",
            "bot_type": "service",
        }
        sync_plugin = MagicMock()
        service = MagicMock()
        listener, factory, dispatcher, resolver = _make_listener(
            service,
            sync_plugin,
            bot_query=bot_query,
        )
        # Published Service bindings are version bindings.  The resolver owns
        # only the current Draft binding, so the identities must never match.
        resolver.resolve_for_bot.return_value.binding_id = 84

        listener.handle(event)

        resolver.resolve_for_bot.assert_called_once_with("service-bot-1", "u001")
        factory.create.assert_not_called()
        dispatcher.dispatch.assert_not_called()
        sync_plugin.sync_symlinks.assert_not_called()

    def test_skips_when_bot_missing_owner(self):
        event = _make_event()
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {"bot_id": "default"}

        listener, _, dispatcher, resolver = _make_listener(bot_query=bot_query)
        listener.handle(event)

        resolver.resolve_for_bot.assert_not_called()
        dispatcher.dispatch.assert_not_called()

    def test_passes_empty_symlink_list_when_no_skills_active(self):
        event = _make_event()

        fake_bot = {"bot_id": "default", "owner_id": "u001"}
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = fake_bot

        fake_sync_plugin = MagicMock()
        fake_sync_plugin.sync_symlinks.return_value = {"success": True}

        fake_service = MagicMock()
        fake_service.get_symlink_mappings.return_value = []

        listener, _, _, _ = _make_listener(fake_service, fake_sync_plugin, bot_query=bot_query)
        listener.handle(event)

        fake_sync_plugin.sync_symlinks.assert_called_once_with([])

    def test_handler_swallows_device_sync_exception(self):
        event = _make_event()

        fake_bot = {"bot_id": "default", "owner_id": "u001"}
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = fake_bot

        fake_sync_plugin = MagicMock()
        fake_sync_plugin.sync_symlinks.side_effect = RuntimeError("network")

        fake_service = MagicMock()
        fake_service.get_symlink_mappings.return_value = []

        listener, _, _, _ = _make_listener(fake_service, fake_sync_plugin, bot_query=bot_query)
        listener.handle(event)

    def test_unclaimed_desktop_keeps_legacy_mapping_writer(self):
        event = _make_event(device_provider="baas")
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {
            "bot_id": "desktop-1",
            "owner_id": "owner-1",
            "entity_id": "entity-1",
            "env": "pre",
            "bot_type": "desktop",
            "active_engine": "openclaw",
        }
        layout_repository = MagicMock()
        layout_repository.get.return_value = _layout_state()
        wakeup = MagicMock()
        sync_plugin = MagicMock()
        sync_plugin.sync_symlinks.return_value = {"success": True}
        service = MagicMock()
        service.get_symlink_mappings.return_value = []
        listener, _, _, _ = _make_listener(
            service,
            sync_plugin,
            bot_query,
            layout_repository,
            wakeup,
        )

        listener.handle(event)

        sync_plugin.sync_symlinks.assert_called_once_with([])
        wakeup.handle.assert_called_once_with(event)

    def test_pool_active_desktop_restores_mapping_with_public_pool_resolver(self):
        event = _make_event(device_provider="baas")
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {
            "bot_id": "desktop-1",
            "owner_id": "owner-1",
            "entity_id": "entity-1",
            "env": "pre",
            "bot_type": "desktop",
            "active_engine": "openclaw",
        }
        layout_repository = MagicMock()
        layout_repository.get.return_value = _layout_state(
            layout=SkillLayout.POOL,
            phase=SkillLayoutPhase.POOL_ACTIVE,
        )
        sync_plugin = MagicMock()
        sync_plugin.sync_symlinks.return_value = {"success": True}
        wakeup = MagicMock()
        service = MagicMock()
        service.get_symlink_mappings.return_value = []
        listener, factory, dispatcher, resolver = _make_listener(
            service,
            sync_plugin,
            bot_query,
            layout_repository,
            wakeup,
        )

        listener.handle(event)

        wakeup.handle.assert_called_once_with(event)
        factory.create.assert_called_once()
        resolver.resolve_for_bot.assert_called_once()
        dispatcher.dispatch.assert_called_once()
        sync_plugin.sync_symlinks.assert_called_once_with([])

    def test_transitional_desktop_mapping_is_left_to_durable_reconciliation(self):
        event = _make_event(device_provider="baas")
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {
            "bot_id": "desktop-1",
            "owner_id": "owner-1",
            "entity_id": "entity-1",
            "env": "pre",
            "bot_type": "desktop",
            "active_engine": "openclaw",
        }
        layout_repository = MagicMock()
        layout_repository.get.return_value = _layout_state(
            phase=SkillLayoutPhase.POOL_ACTIVATING_PRE_CUTOVER,
        )
        sync_plugin = MagicMock()
        wakeup = MagicMock()
        listener, factory, dispatcher, resolver = _make_listener(
            MagicMock(),
            sync_plugin,
            bot_query,
            layout_repository,
            wakeup,
        )

        listener.handle(event)

        wakeup.handle.assert_called_once_with(event)
        factory.create.assert_not_called()
        resolver.resolve_for_bot.assert_not_called()
        dispatcher.dispatch.assert_not_called()
        sync_plugin.sync_symlinks.assert_not_called()

    def test_cutover_started_during_legacy_sync_reenqueues_convergence(self):
        event = _make_event(device_provider="baas")
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {
            "bot_id": "desktop-1",
            "owner_id": "owner-1",
            "entity_id": "entity-1",
            "env": "pre",
            "bot_type": "desktop",
            "active_engine": "openclaw",
        }
        layout_repository = MagicMock()
        layout_repository.get.side_effect = [
            _layout_state(),
            _layout_state(
                phase=SkillLayoutPhase.POOL_ACTIVATING_PRE_CUTOVER,
            ),
        ]
        wakeup = MagicMock()
        sync_plugin = MagicMock()
        sync_plugin.sync_symlinks.return_value = {"success": True}
        service = MagicMock()
        service.get_symlink_mappings.return_value = []
        listener, _, _, _ = _make_listener(
            service,
            sync_plugin,
            bot_query,
            layout_repository,
            wakeup,
        )

        listener.handle(event)

        sync_plugin.sync_symlinks.assert_called_once_with([])
        assert wakeup.handle.call_args_list == [call(event), call(event)]

    def test_desktop_wakeup_failure_preserves_legacy_mapping_refresh(self):
        event = _make_event(device_provider="baas")
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {
            "bot_id": "desktop-1",
            "owner_id": "owner-1",
            "entity_id": "entity-1",
            "env": "pre",
            "bot_type": "desktop",
            "active_engine": "openclaw",
        }
        layout_repository = MagicMock()
        layout_repository.get.return_value = _layout_state()
        wakeup = MagicMock()
        wakeup.handle.side_effect = RuntimeError("queue unavailable")
        sync_plugin = MagicMock()
        sync_plugin.sync_symlinks.return_value = {"success": True}
        service = MagicMock()
        service.get_symlink_mappings.return_value = []
        listener, _, _, _ = _make_listener(
            service,
            sync_plugin,
            bot_query,
            layout_repository,
            wakeup,
        )

        listener.handle(event)

        wakeup.handle.assert_called_once_with(event)
        sync_plugin.sync_symlinks.assert_called_once_with([])

    def test_desktop_layout_lookup_failure_preserves_legacy_mapping_refresh(self):
        event = _make_event(device_provider="baas")
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {
            "bot_id": "desktop-1",
            "owner_id": "owner-1",
            "entity_id": "entity-1",
            "env": "pre",
            "bot_type": "desktop",
            "active_engine": "openclaw",
        }
        layout_repository = MagicMock()
        layout_repository.get.side_effect = RuntimeError("layout unavailable")
        wakeup = MagicMock()
        sync_plugin = MagicMock()
        sync_plugin.sync_symlinks.return_value = {"success": True}
        service = MagicMock()
        service.get_symlink_mappings.return_value = []
        listener, _, _, _ = _make_listener(
            service,
            sync_plugin,
            bot_query,
            layout_repository,
            wakeup,
        )

        listener.handle(event)

        wakeup.handle.assert_called_once_with(event)
        sync_plugin.sync_symlinks.assert_called_once_with([])
