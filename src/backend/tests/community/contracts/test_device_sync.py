"""Rule 25 conformance — DeviceSyncPlugin.

Consumer under test: ``DeviceSyncDispatcher.dispatch(ctx)`` returns a
``DeviceSyncPlugin`` keyed by ``ctx.provider``. In local mode the
testing dispatcher binding returns a ``LocalDeviceSyncPlugin`` pointing
at a shared skills_dir; the plugin's ``sync_symlinks`` reconciles the
directory against a desired mapping list.

Phase 2 Task 6 收口后 ``DeviceSyncPluginSupplier`` Protocol 已删, dispatch
入口统一走 ``DeviceSyncDispatcher`` —— 本契约测试改成调 ``LocalDeviceSyncPlugin``
直接验证 plugin 行为(consumer-level conformance,与原签同义)。

Plugin-hit assertion: with an empty mapping list and a ``tmp_path``
skills_dir, ``sync_symlinks`` must return ``success=True`` and create
the directory on disk. The dir existence is observable proof the plugin
executed; an empty-list short-circuit would not produce it.
"""
from __future__ import annotations

from agentclaw.community.plugins.local.device_sync import LocalDeviceSyncPlugin


def test_local_plugin_actually_runs(tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    plugin = LocalDeviceSyncPlugin(skills_dir=skills_dir)
    result = plugin.sync_symlinks([])

    # Plugin-hit assertion: the local impl mkdir's the skills_dir and
    # returns success — observable proof the plugin ran.
    assert result["success"] is True
    assert skills_dir.is_dir()


def test_local_plugin_serves_mcp_methods(tmp_path) -> None:
    """The DeviceSyncPlugin now also carries MCP delivery (folded in from the
    former DeviceMCPSyncPlugin). In local mode every MCP method is a no-op that
    reports success / present, so the boundary is uniformly callable."""
    skills_dir = tmp_path / "skills"
    plugin = LocalDeviceSyncPlugin(skills_dir=skills_dir)

    assert plugin.sync_all_mcp_servers([{"server_code": "svc_a"}]) is True
    assert plugin.sync_single_mcp({"server_code": "svc_a"}) is True
    assert plugin.sync_remove_mcp("svc_a") is True
    assert plugin.has_mcp("svc_a") is True
