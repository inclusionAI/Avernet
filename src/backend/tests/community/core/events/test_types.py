"""Tests for agentclaw.community.core.events.types."""

from __future__ import annotations

from agentclaw.community.core.events.types import DeviceActivatedEvent


class TestDeviceActivatedEvent:
    def test_all_fields_accessible(self):
        event = DeviceActivatedEvent(
            device_id="staff_u001_default",
            binding_id=42,
            entity_id="u001",
            entity_type="staff",
            device_provider="arca",
            sandbox_id="sbx-123@alt-0",
        )
        assert event.device_id == "staff_u001_default"
        assert event.binding_id == 42
        assert event.entity_id == "u001"
        assert event.entity_type == "staff"
        assert event.device_provider == "arca"
        assert event.sandbox_id == "sbx-123@alt-0"

    def test_sandbox_id_defaults_to_none_for_non_sandbox_device(self):
        event = DeviceActivatedEvent(
            device_id="staff_u001_default",
            binding_id=42,
            entity_id="u001",
            entity_type="staff",
            device_provider="local",
        )
        assert event.sandbox_id is None
