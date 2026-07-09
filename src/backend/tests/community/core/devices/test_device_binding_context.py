"""Tests for DeviceBindingContext — plugin-side per-bot binding snapshot."""
from agentclaw.community.core.devices.models import DeviceBindingContext


def test_device_binding_context_minimum_fields():
    ctx = DeviceBindingContext(
        binding_id=42,
        device_id="bot-uuid-001",
        entity_id="staff_u001",
        adapter_port=20010,
    )
    assert ctx.binding_id == 42
    assert ctx.device_id == "bot-uuid-001"
    assert ctx.entity_id == "staff_u001"
    assert ctx.adapter_port == 20010
    # tenant 默认空
    assert ctx.tenant == ""


def test_device_binding_context_with_tenant():
    ctx = DeviceBindingContext(
        binding_id=42,
        device_id="bot-uuid-001",
        entity_id="staff_u001",
        adapter_port=20010,
        tenant="team_claw",
    )
    assert ctx.tenant == "team_claw"
