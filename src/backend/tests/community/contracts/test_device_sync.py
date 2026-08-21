"""Contract tests for the Core local ``DeviceSync`` service.

The tests verify that the pathlib implementation executes against a configured
skills directory and that the complete six-method contract remains callable.
"""
from __future__ import annotations

from agentclaw.community.core.devices.services.local_device_sync import (
    LocalDeviceSyncService,
)


def test_local_service_actually_runs(tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    service = LocalDeviceSyncService(skills_dir=skills_dir)
    result = service.sync_symlinks([])

    # The local service creates the configured directory before reconciling it.
    assert result["success"] is True
    assert skills_dir.is_dir()


def test_local_service_serves_mcp_methods(tmp_path) -> None:
    """Local-mode MCP methods are no-ops that report success/present."""
    skills_dir = tmp_path / "skills"
    service = LocalDeviceSyncService(skills_dir=skills_dir)

    assert service.sync_all_mcp_servers([{"server_code": "svc_a"}]) is True
    assert service.sync_single_mcp({"server_code": "svc_a"}) is True
    assert service.sync_remove_mcp("svc_a") is True
    assert service.has_mcp("svc_a") is True
