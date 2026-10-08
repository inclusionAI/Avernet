"""Tests for local-mode MCP Center behavior."""
from __future__ import annotations

import json

from agentclaw.community.plugins.local.mcp_center import NoopMCPCenterPlugin


def _set_env_local_mcp_config(monkeypatch, tmp_path):
    path = tmp_path / "local_mcp.json"
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "serverCode": "mcp.local.demo",
                        "name": "Local Demo",
                        "runMode": "LOCAL",
                        "status": "ONLINE",
                        "accessLevel": "LOCAL",
                        "stdioConfigs": [
                            {
                                "command": "node",
                                "arguments": ["server.js"],
                                "envVariables": {"TOKEN": "abc"},
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTCLAW_LOCAL_MCP_CONFIG", str(path))


def test_get_mcp_detail_ignores_prod_local_mcp_config(monkeypatch, tmp_path):
    _set_env_local_mcp_config(monkeypatch, tmp_path)

    detail = NoopMCPCenterPlugin().get_mcp_detail("mcp.local.demo")

    assert detail is None


def test_get_mcp_list_ignores_prod_local_mcp_config(monkeypatch, tmp_path):
    _set_env_local_mcp_config(monkeypatch, tmp_path)

    result = NoopMCPCenterPlugin().get_mcp_list(server_codes=["mcp.local.demo"])

    assert result["success"] is True
    assert result["total"] == 0
    assert result["data"] == []


def test_local_mode_permission_keeps_existing_fail_open_default(monkeypatch, tmp_path):
    _set_env_local_mcp_config(monkeypatch, tmp_path)

    result = NoopMCPCenterPlugin().check_mcp_permission_detail("user1", "mcp.local.demo")

    assert result == {
        "has_permission": True,
        "access_level": "LOCAL",
        "tool_permissions": {},
    }
