"""End-to-end: report_device_alive → EventBus → skill_symlink_listener → sync_symlinks."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.events.bus import get_event_bus, reset_event_bus
from agentclaw.community.core.events.types import DeviceActivatedEvent


@pytest.fixture(autouse=True)
def _reset_bus():
    reset_event_bus()
    yield
    reset_event_bus()


def _pending_record() -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=7,
        entity_id="u001",
        entity_type="staff",
        device_id="staff_u001_default",
        device_provider="arca",
        env="dev",
        device_props={"callback_token": "tok", "sandbox_id": "sbx-xyz"},
        status=DeviceBindingStatus.PENDING.value,
        apply_reason=None,
        applied_by="u001",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=datetime(2024, 1, 1),
        gmt_modified=datetime(2024, 1, 1),
    )


def test_report_device_alive_triggers_full_symlink_sync():
    record = _pending_record()
    updated = _pending_record()
    # pretend update flipped status to ACTIVE
    object.__setattr__(updated, "status", DeviceBindingStatus.ACTIVE.value)

    repo = MagicMock()
    repo.get_by_device_id.return_value = record
    repo.get_by_id.return_value = updated

    bot = {"bot_id": "default", "owner_id": "u001"}
    bot_query = MagicMock()
    bot_query.get_by_binding_id.return_value = bot
    bot_sync = MagicMock()
    bot_sync.sync_bot_config_to_device.return_value = {"success": True}

    fake_sync_plugin = MagicMock()
    fake_sync_plugin.sync_symlinks.return_value = {"success": True, "message": "ok"}

    fake_service = MagicMock()
    fake_service.get_symlink_mappings.return_value = [
        MagicMock(to_dict=lambda: {"source": "/x", "target": "/y"})
    ]

    from agentclaw.community.core.skill_center.services.skill_symlink_listener import (
        SkillSymlinkListener,
    )
    mock_factory = MagicMock()
    mock_factory.create.return_value = fake_service
    mock_resolver = MagicMock()
    mock_ctx = MagicMock()
    mock_resolver.resolve_for_bot.return_value = mock_ctx
    mock_dispatcher = MagicMock()
    mock_dispatcher.dispatch.return_value = fake_sync_plugin

    # Build the listener with mocks directly and subscribe its handler.
    listener = SkillSymlinkListener(
        bot_repo=bot_query,
        skill_set_factory=mock_factory,
        resolver=mock_resolver,
        device_sync_dispatcher=mock_dispatcher,
    )
    get_event_bus().subscribe(DeviceActivatedEvent, listener.handle)

    captured_event: list[DeviceActivatedEvent] = []

    def capture_event(event: DeviceActivatedEvent) -> None:
        captured_event.append(event)

    get_event_bus().subscribe(DeviceActivatedEvent, capture_event)

    svc = DeviceService(
        repository=repo,
        bot_query=bot_query,
        bot_sync=bot_sync,
        oss_record_repo=MagicMock(),
        mcp_sync=MagicMock(),
    )
    svc.report_device_alive(device_id="staff_u001_default", token="tok")

    assert len(captured_event) == 1, "Expected exactly one DeviceActivatedEvent"
    evt = captured_event[0]
    assert evt.device_id == "staff_u001_default"
    assert evt.binding_id == 7
    assert evt.entity_id == "u001"
    assert evt.entity_type == "staff"
    assert evt.device_provider == "arca"
    assert evt.sandbox_id == "sbx-xyz"

    fake_sync_plugin.sync_symlinks.assert_called_once_with(
        [{"source": "/x", "target": "/y"}]
    )
    mock_resolver.resolve_for_bot.assert_called_once_with("default", "u001")
    mock_dispatcher.dispatch.assert_called_once_with(mock_ctx)


def test_report_device_alive_skip_token_check_bypasses_token_validation():
    """When skip_token_check=True, report_device_alive should bypass token validation.

    This is needed for in-process calls from BaaS publish callback where the agentbox
    binding has no callback_token configured, but we still need to trigger the
    DeviceActivatedEvent to drive SkillSymlinkListener.
    """
    record = _pending_record()
    updated = _pending_record()
    object.__setattr__(updated, "status", DeviceBindingStatus.ACTIVE.value)

    repo = MagicMock()
    repo.get_by_device_id.return_value = record
    repo.get_by_id.return_value = updated

    bot = {"bot_id": "default", "owner_id": "u001"}
    bot_query = MagicMock()
    bot_query.get_by_binding_id.return_value = bot
    bot_sync = MagicMock()
    bot_sync.sync_bot_config_to_device.return_value = {"success": True}

    fake_sync_plugin = MagicMock()
    fake_sync_plugin.sync_symlinks.return_value = {"success": True, "message": "ok"}

    fake_service = MagicMock()
    fake_service.get_symlink_mappings.return_value = [
        MagicMock(to_dict=lambda: {"source": "/x", "target": "/y"})
    ]

    from agentclaw.community.core.skill_center.services.skill_symlink_listener import (
        SkillSymlinkListener,
    )
    mock_factory = MagicMock()
    mock_factory.create.return_value = fake_service
    mock_resolver = MagicMock()
    mock_dispatcher = MagicMock()
    mock_dispatcher.dispatch.return_value = fake_sync_plugin

    listener = SkillSymlinkListener(
        bot_repo=bot_query,
        skill_set_factory=mock_factory,
        resolver=mock_resolver,
        device_sync_dispatcher=mock_dispatcher,
    )
    get_event_bus().subscribe(DeviceActivatedEvent, listener.handle)

    captured_event: list[DeviceActivatedEvent] = []

    def capture_event(event: DeviceActivatedEvent) -> None:
        captured_event.append(event)

    get_event_bus().subscribe(DeviceActivatedEvent, capture_event)

    svc = DeviceService(
        repository=repo,
        bot_query=bot_query,
        bot_sync=bot_sync,
        oss_record_repo=MagicMock(),
        mcp_sync=MagicMock(),
    )

    # Call with WRONG token but skip_token_check=True - should NOT raise
    svc.report_device_alive(
        device_id="staff_u001_default",
        token="wrong_token",
        skip_token_check=True,
    )

    # Should still have published the event despite wrong token
    assert len(captured_event) == 1, "Expected exactly one DeviceActivatedEvent"
    evt = captured_event[0]
    assert evt.device_id == "staff_u001_default"
    assert evt.binding_id == 7
