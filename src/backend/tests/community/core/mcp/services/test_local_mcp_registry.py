"""Tests for local MCP registry config loading."""
from __future__ import annotations

import builtins
import json

from agentclaw.community.core.mcp.services.local_mcp_registry import (
    LOCAL_MCP_CONFIG_FILENAME,
    LocalMCPRegistry,
)


def _write_config(tmp_path, payload, filename="local_mcp.json"):
    path = tmp_path / filename
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_loads_and_normalizes_local_stdio_mcp(monkeypatch, tmp_path):
    config_path = _write_config(
        tmp_path,
        {
            "servers": [
                {
                    "server_code": "mcp.local.demo",
                    "name": "Local Demo",
                    "description": "demo server",
                    "stdio_configs": [
                        {
                            "command": "node",
                            "arguments": ["server.js"],
                            "envVariables": {"TOKEN": "abc"},
                        }
                    ],
                }
            ]
        },
    )

    detail = LocalMCPRegistry(config_path=config_path).get_mcp_detail("mcp.local.demo")

    assert detail is not None
    assert detail["serverCode"] == "mcp.local.demo"
    assert detail["server_code"] == "mcp.local.demo"
    assert detail["runMode"] == "LOCAL"
    assert detail["run_mode"] == "LOCAL"
    assert detail["status"] == "ONLINE"
    assert detail["accessLevel"] == "LOCAL"
    assert detail["source"] == "local"
    assert detail["stdioConfigs"][0]["command"] == "node"
    assert detail["stdio_configs"][0]["arguments"] == ["server.js"]


def test_list_filters_by_server_code_search_and_run_mode(monkeypatch, tmp_path):
    config_path = _write_config(
        tmp_path,
        {
            "servers": [
                {"serverCode": "mcp.local.one", "name": "Local One"},
                {"serverCode": "mcp.local.two", "name": "Other", "runMode": "REMOTE"},
            ]
        },
    )

    registry = LocalMCPRegistry(config_path=config_path)

    assert [m["serverCode"] for m in registry.list_mcp_details(server_codes=["mcp.local.one"])] == [
        "mcp.local.one"
    ]
    assert [m["serverCode"] for m in registry.list_mcp_details(search_key="one")] == [
        "mcp.local.one"
    ]
    assert [m["serverCode"] for m in registry.list_mcp_details(run_modes=["LOCAL"])] == [
        "mcp.local.one"
    ]


def test_loads_keyed_stdio_mcp_config(monkeypatch, tmp_path):
    config_path = _write_config(
        tmp_path,
        {
            "servers": {
                "hitl": {
                    "command": "python3",
                    "args": ["/home/admin/hitl/hitl_mcp_server.py"],
                    "env": {"SESSION": "demo"},
                }
            }
        },
    )

    detail = LocalMCPRegistry(config_path=config_path).get_mcp_detail("hitl")

    assert detail is not None
    assert detail["serverCode"] == "hitl"
    assert detail["server_code"] == "hitl"
    assert detail["runMode"] == "LOCAL"
    assert detail["stdioConfigs"] == [
        {
            "command": "python3",
            "arguments": ["/home/admin/hitl/hitl_mcp_server.py"],
            "envVariables": {"SESSION": "demo"},
        }
    ]


def test_missing_config_returns_empty(tmp_path):
    registry = LocalMCPRegistry(config_path=tmp_path / "missing.yaml")

    assert registry.get_mcp_detail("mcp.local.missing") is None
    assert registry.list_mcp_details() == []


def test_environment_variable_is_ignored(monkeypatch, tmp_path):
    config_path = _write_config(
        tmp_path,
        {"servers": [{"serverCode": "mcp.local.from.env", "name": "Env MCP"}]},
    )
    monkeypatch.setenv("AGENTCLAW_LOCAL_MCP_CONFIG", str(config_path))

    registry = LocalMCPRegistry(config_path=tmp_path / "missing.yaml")

    assert registry.get_mcp_detail("mcp.local.from.env") is None
    assert registry.list_mcp_details() == []


def test_default_config_path_loads_repo_hitl_config():
    detail = LocalMCPRegistry().get_mcp_detail("hitl")

    assert detail is not None
    assert detail["stdioConfigs"][0]["command"] == "python3"
    assert detail["stdioConfigs"][0]["arguments"] == ["/home/admin/hitl/hitl_mcp_server.py"]


def test_default_config_path_loads_repo_clawmind_config():
    detail = LocalMCPRegistry().get_mcp_detail("clawmind")

    assert detail is not None
    assert detail["serverCode"] == "clawmind"
    assert detail["runMode"] == "LOCAL"
    assert detail["stdioConfigs"][0]["command"] == "node"
    assert detail["stdioConfigs"][0]["arguments"] == [
        "/home/admin/clawmind-mcp/dist/esm/platform/mcp-entry.js"
    ]
    env = detail["stdioConfigs"][0].get("envVariables", {})
    assert env.get("MCP_TRANSPORT") == "stdio"
    assert env.get("CCT_SOP_MCP_SERVER_MODE") == "prod"
    # SKILL_ROOT, DATABASE_MODE and CLAWWEB_API_URL come from ClawMind's own
    # configs/application.yaml / process.cwd() fallback, not from mcporter.json env


def test_default_config_path_falls_back_when_repo_marker_missing(monkeypatch):
    monkeypatch.setattr("agentclaw.community.core.mcp.services.local_mcp_registry.Path", _NoRepoPath)

    config_path = LocalMCPRegistry()._default_config_path()

    assert str(config_path) == f"configs/{LOCAL_MCP_CONFIG_FILENAME}"


def test_top_level_list_yaml_and_filter_matrix(tmp_path):
    config_path = tmp_path / "local-mcp-servers.yaml"
    config_path.write_text(
        """
- serverCode: mcp.local.rich
  name: Rich Local
  description: searchable rich server
  platformServerCode: platform-rich
  runMode: local
  status: online
  hostPlatform: host-a
  owners:
    - owner-a
  networkType: OFFICE
  categoryCode: cat-a
  tenantCode: tenant-a
  endpoints:
    - transportProtocol: SSE
  stdioConfigs:
    - command: python3
- serverCode: mcp.local.other
  name: Other
  runMode: REMOTE
  status: offline
  endpoints:
    - transportProtocol: STREAMABLE_HTTP
""",
        encoding="utf-8",
    )
    registry = LocalMCPRegistry(config_path=config_path)

    for kwargs in [
        {"search_key": "RICH"},
        {"platform_server_codes": ["platform-rich"]},
        {"run_modes": ["LOCAL"]},
        {"statuses": ["ONLINE"]},
        {"transport_protocols": ["SSE"]},
        {"transport_protocols": ["STDIO"]},
        {"host_platforms": ["host-a"]},
        {"owners": ["owner-a"]},
        {"network_types": ["OFFICE"]},
        {"categories": ["cat-a"]},
        {"tenants": ["tenant-a"]},
    ]:
        assert [m["serverCode"] for m in registry.list_mcp_details(**kwargs)] == [
            "mcp.local.rich"
        ]

    for kwargs in [
        {"search_key": "missing"},
        {"platform_server_codes": ["platform-missing"]},
        {"run_modes": ["REMOTE_ONLY"]},
        {"statuses": ["DISABLED"]},
        {"transport_protocols": ["WEBSOCKET"]},
        {"host_platforms": ["host-missing"]},
        {"owners": ["owner-missing"]},
        {"network_types": ["INTRANET"]},
        {"categories": ["cat-missing"]},
        {"tenants": ["tenant-missing"]},
        {"server_codes": ["mcp.local.missing"]},
    ]:
        assert registry.list_mcp_details(**kwargs) == []


def test_invalid_entries_and_shapes_are_ignored(tmp_path):
    config_path = _write_config(
        tmp_path,
        {
            "servers": [
                "bad-entry",
                {"name": "Missing Code"},
                {"serverCode": "mcp.local.no-stdio", "stdioConfigs": "not-a-list"},
            ]
        },
    )

    servers = LocalMCPRegistry(config_path=config_path).list_mcp_details()

    assert [server["serverCode"] for server in servers] == ["mcp.local.no-stdio"]
    assert "stdio_configs" not in servers[0]


def test_invalid_mapping_entries_and_raw_shapes_return_empty(tmp_path):
    mapping_config = _write_config(
        tmp_path,
        {"servers": {"bad": "not-a-dict"}},
        filename="mapping.json",
    )
    scalar_config = tmp_path / "scalar.json"
    scalar_config.write_text('"not-a-list-or-dict"', encoding="utf-8")
    bad_servers_config = _write_config(
        tmp_path,
        {"servers": "not-a-list-or-dict"},
        filename="bad_servers.json",
    )

    assert LocalMCPRegistry(config_path=mapping_config).list_mcp_details() == []
    assert LocalMCPRegistry(config_path=scalar_config).list_mcp_details() == []
    assert LocalMCPRegistry(config_path=bad_servers_config).list_mcp_details() == []


def test_yaml_without_pyyaml_returns_empty(monkeypatch, tmp_path):
    config_path = tmp_path / "local-mcp-servers.yaml"
    config_path.write_text("servers: []", encoding="utf-8")
    real_import = builtins.__import__

    def _raise_for_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("missing yaml")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _raise_for_yaml)

    assert LocalMCPRegistry(config_path=config_path).list_mcp_details() == []


def test_unreadable_paths_and_parse_errors_return_empty(tmp_path):
    directory_path = tmp_path / "config-dir"
    directory_path.mkdir()
    invalid_json = tmp_path / "bad.json"
    invalid_json.write_text("{not valid json", encoding="utf-8")

    assert LocalMCPRegistry(config_path=directory_path).list_mcp_details() == []
    assert LocalMCPRegistry(config_path=invalid_json).list_mcp_details() == []


class _NoRepoPath:
    def __init__(self, value):
        self.value = str(value)

    def __truediv__(self, child):
        return _NoRepoPath(f"{self.value}/{child}")

    def __str__(self):
        return self.value

    def __fspath__(self):
        return self.value

    @classmethod
    def __call__(cls, value):
        return cls(value)

    @classmethod
    def __getattr__(cls, name):
        if name == "__file__":
            return cls("missing.py")
        raise AttributeError(name)

    def expanduser(self):
        return self

    def exists(self):
        return False

    def is_file(self):
        return False

    def read_text(self, encoding="utf-8"):
        raise AssertionError("fallback path should not be read")

    def resolve(self):
        return self

    @property
    def parents(self):
        return [_NoRepoPath("/no-repo/src"), _NoRepoPath("/no-repo")]

    @property
    def suffix(self):
        return ""
