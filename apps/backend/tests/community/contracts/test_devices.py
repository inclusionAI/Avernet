"""Rule 25 conformance — DeviceAccessor (local impl).

DeviceAccessor's local impl (``LocalDeviceAccessor``) returns ``None`` from
``get_connection_info`` by design — there is no remote device locally.
Downstream consumers rely on that ``None`` to fall back to local/no-op
behavior: the prod device-sync supplier raises ``DeviceSyncUnavailableError``
(→ "missing device" error), and ``SkillSetService`` leaves
``device_provider=None``.

(Previously this suite drove ``MCPSyncService.sync_mcp_detail``; that service is
now provider-blind — it resolves a per-bot plugin from
``DeviceSyncPluginSupplier`` and no longer consumes ``DeviceAccessor`` directly,
so in local mode an MCP edit is a no-op success rather than a missing-device
error.)

Here we pin the local-impl contract through the DI-bound plugin.
"""
from __future__ import annotations

from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor


def test_local_device_plugin_returns_no_connection(world) -> None:
    plugin = world.get(DeviceAccessor)
    # No remote device locally — a (non-existent) local bot resolves to the
    # local impl, which returns None. This None is the contract downstream
    # consumers branch on.
    assert plugin.get_connection_info("bot_x", "alice") is None
