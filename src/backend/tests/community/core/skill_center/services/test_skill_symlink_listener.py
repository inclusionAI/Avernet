"""Tests for agentclaw.community.core.skill_center.services.skill_symlink_listener."""

from __future__ import annotations

from unittest.mock import MagicMock

from agentclaw.community.core.events.types import DeviceActivatedEvent
from agentclaw.community.core.skill_center.services.skill_symlink_listener import (
    SkillSymlinkListener,
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


def _make_listener(skill_set_service=None, sync_plugin=None, bot_query=None):
    factory = MagicMock()
    if skill_set_service is not None:
        factory.create.return_value = skill_set_service

    resolver = MagicMock()
    fake_ctx = MagicMock()
    resolver.resolve_for_bot.return_value = fake_ctx

    dispatcher = MagicMock()
    if sync_plugin is not None:
        dispatcher.dispatch.return_value = sync_plugin

    bot_repo = bot_query if bot_query is not None else MagicMock()

    listener = SkillSymlinkListener(
        bot_repo=bot_repo,
        skill_set_factory=factory,
        resolver=resolver,
        device_sync_dispatcher=dispatcher,
    )
    return listener, factory, dispatcher, resolver


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
