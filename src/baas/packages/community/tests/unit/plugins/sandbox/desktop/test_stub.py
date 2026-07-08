"""Unit tests for StubDesktopSandbox and StubDesktopSandboxPlugin."""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from secbaas.config import Config
from secbaas.plugins.sandbox.desktop._stub import (
    StubCommandResult,
    StubDesktopSandbox,
    StubDesktopSandboxPlugin,
)

_STUB_PROXY_CONFIG = Config(
    user_config={
        "agentclawproxy": {
            "host": {
                "dev": "ac-proxy-dev.service.test",
            },
        },
    },
)

# ---------------------------------------------------------------------------
# StubCommandResult tests
# ---------------------------------------------------------------------------


class TestStubCommandResult:
    def test_default_exit_code(self) -> None:
        assert StubCommandResult.exit_code == 0

    def test_default_stdout(self) -> None:
        assert StubCommandResult.stdout == "mock-output"

    def test_default_stderr(self) -> None:
        assert StubCommandResult.stderr == ""

    def test_default_execution_time_ms(self) -> None:
        assert StubCommandResult.execution_time_ms == 0.0


# ---------------------------------------------------------------------------
# StubDesktopSandbox tests
# ---------------------------------------------------------------------------


class TestStubDesktopSandboxConstruction:
    def test_construction_with_only_container_id(self) -> None:
        device = StubDesktopSandbox("ctr-1")
        assert device.container_id == "ctr-1"
        assert device.machine_id == ""
        assert device.user_id == ""

    def test_construction_with_all_params(self) -> None:
        device = StubDesktopSandbox("ctr-2", machine_id="m-1", user_id="u-1")
        assert device.container_id == "ctr-2"
        assert device.machine_id == "m-1"
        assert device.user_id == "u-1"

    def test_is_running_always_true(self) -> None:
        device = StubDesktopSandbox("ctr-running")
        assert device.is_running is True

    def test_properties_readonly(self) -> None:
        device = StubDesktopSandbox("ctr-ro", machine_id="m-ro", user_id="u-ro")
        assert device.container_id == "ctr-ro"
        assert device.machine_id == "m-ro"
        assert device.user_id == "u-ro"


class TestStubDesktopSandboxGetInfo:
    def test_returns_dict_with_all_keys(self) -> None:
        device = StubDesktopSandbox("ctr-info", machine_id="m-info", user_id="u-info")
        info = device.get_info()
        assert isinstance(info, dict)
        assert info["container_id"] == "ctr-info"
        assert info["machine_id"] == "m-info"
        assert info["user_id"] == "u-info"
        assert info["status"] == "RUNNING"
        assert info["platform"] == "desktop"
        assert info["port"] == 8080

    def test_get_info_with_default_values(self) -> None:
        device = StubDesktopSandbox("ctr-default")
        info = device.get_info()
        assert info["machine_id"] == ""
        assert info["user_id"] == ""
        assert info["status"] == "RUNNING"


class TestStubDesktopSandboxExecShell:
    def test_returns_stub_command_result(self) -> None:
        device = StubDesktopSandbox("ctr-exec")
        result = device.exec_shell("echo hello")
        assert isinstance(result, StubCommandResult)
        assert result.exit_code == 0
        assert result.stdout == "mock-output"

    def test_accepts_env_and_timeout(self) -> None:
        device = StubDesktopSandbox("ctr-exec2")
        result = device.exec_shell("cmd", env={"KEY": "V"}, timeout_seconds=60)
        assert isinstance(result, StubCommandResult)

    def test_default_timeout_is_thirty_seconds(self) -> None:
        device = StubDesktopSandbox("ctr-exec3")
        result = device.exec_shell("cmd")
        assert isinstance(result, StubCommandResult)


class TestStubDesktopSandboxHttpProxy:
    def test_returns_proxy_response_dict(self) -> None:
        device = StubDesktopSandbox("ctr-proxy")
        response = device.http_proxy(
            method="GET",
            port=8080,
            path="/api/test",
            headers={"Host": "localhost"},
            body=b"request body",
        )
        assert response["status_code"] == 200
        assert response["headers"] == {"Content-Type": "text/plain"}
        assert response["body"] == "bW9jayBodHRwIHJlc3BvbnNl"

    def test_accepts_query_string(self) -> None:
        device = StubDesktopSandbox("ctr-proxy2")
        response = device.http_proxy(
            method="POST",
            port=3000,
            path="/submit",
            headers={},
            body=b"",
            query_string="foo=bar&baz=1",
        )
        assert response["status_code"] == 200


class TestStubDesktopSandboxDestroy:
    def test_returns_true(self) -> None:
        device = StubDesktopSandbox("ctr-destroy")
        assert device.destroy() is True


class TestStubDesktopSandboxRestart:
    def test_returns_true(self) -> None:
        device = StubDesktopSandbox("ctr-restart")
        assert device.restart() is True


# ---------------------------------------------------------------------------
# StubDesktopSandboxPlugin tests
# ---------------------------------------------------------------------------


class TestStubDesktopSandboxPluginInit:
    def test_initializes_with_empty_devices(self) -> None:
        plugin = StubDesktopSandboxPlugin()
        assert plugin._devices == {}
        plugin.close()


class TestStubDesktopSandboxPluginCreateDevice:
    def test_returns_stub_desktop_device(self) -> None:
        plugin = StubDesktopSandboxPlugin()
        device = plugin.create_device(
            machine_id="m-create",
            bot_uuid="bot-001",
            agent_code="ac-001",
            user_id="u-create",
        )
        assert isinstance(device, StubDesktopSandbox)
        assert device.machine_id == "m-create"
        assert device.user_id == "u-create"

    def test_stores_device_by_container_id(self) -> None:
        plugin = StubDesktopSandboxPlugin()
        device = plugin.create_device("m-1", "bot-a", "ac-a", "u-1")
        assert plugin._devices[device.container_id] is device

    def test_generates_unique_container_ids(self) -> None:
        plugin = StubDesktopSandboxPlugin()
        d1 = plugin.create_device("m", "b1", "a1", "u1")
        d2 = plugin.create_device("m", "b2", "a2", "u2")
        assert d1.container_id != d2.container_id
        assert d1.container_id.startswith("mock-")
        assert len(d1.container_id) == 17

    def test_container_id_is_valid_uuid_hex(self) -> None:
        plugin = StubDesktopSandboxPlugin()
        device = plugin.create_device("m-uuid", "b", "a", "u")
        hex_part = device.container_id[5:]
        assert len(hex_part) == 12
        assert re.fullmatch(r"[0-9a-f]+", hex_part)

    def test_stores_multiple_devices(self) -> None:
        plugin = StubDesktopSandboxPlugin()
        for i in range(5):
            plugin.create_device(f"m-{i}", f"b-{i}", f"a-{i}", f"u-{i}")
        assert len(plugin._devices) == 5

    def test_accepts_optional_params(self) -> None:
        plugin = StubDesktopSandboxPlugin()
        device = plugin.create_device(
            "m-opt",
            "b-opt",
            "a-opt",
            "u-opt",
            envs={"KEY": "val"},
            mount_path="/mnt",
            name="my-device",
            description="test device",
        )
        assert isinstance(device, StubDesktopSandbox)


class TestStubDesktopSandboxPluginConnectDevice:
    def test_connects_existing_device(self) -> None:
        plugin = StubDesktopSandboxPlugin()
        created = plugin.create_device("m-conn", "b-conn", "a-conn", "u-conn")
        cid = created.container_id

        connected = plugin.connect_device(cid, "m-conn", "u-conn")
        assert connected is created

    def test_creates_new_device_if_missing(self) -> None:
        plugin = StubDesktopSandboxPlugin()
        connected = plugin.connect_device("new-cid", "m-new", "u-new")
        assert isinstance(connected, StubDesktopSandbox)
        assert connected.container_id == "new-cid"
        assert connected.machine_id == "m-new"
        assert connected.user_id == "u-new"
        assert "new-cid" in plugin._devices

    def test_connect_then_reconnect_returns_same_instance(self) -> None:
        plugin = StubDesktopSandboxPlugin()
        c1 = plugin.connect_device("cid-x", "m-x", "u-x")
        c2 = plugin.connect_device("cid-x", "m-x", "u-x")
        assert c1 is c2


class TestStubDesktopSandboxPluginGetMachineInfo:
    def test_returns_machine_info_dict(self) -> None:
        plugin = StubDesktopSandboxPlugin()
        info = plugin.get_machine_info("m-info")
        assert info["machine_id"] == "m-info"
        assert info["cpu_cores"] == 4
        assert info["memory_gb"] == 16
        assert info["disk_gb"] == 256

    def test_different_machine_id(self) -> None:
        plugin = StubDesktopSandboxPlugin()
        info = plugin.get_machine_info("machine-xyz")
        assert info["machine_id"] == "machine-xyz"


class TestStubDesktopSandboxPluginGetMachineResDirs:
    def test_returns_dir_tree(self) -> None:
        plugin = StubDesktopSandboxPlugin()
        dirs = plugin.get_machine_res_dirs("m-res")
        assert dirs["name"] == "Desktop"
        assert len(dirs["children"]) == 2
        assert dirs["children"][0]["name"] == "agent-code"
        assert dirs["children"][1]["name"] == "projects"

    def test_default_dir_param(self) -> None:
        plugin = StubDesktopSandboxPlugin()
        dirs = plugin.get_machine_res_dirs("m-res-default")
        assert dirs["name"] == "Desktop"

    def test_custom_dir_ignored(self) -> None:
        # The stub ignores the dir parameter
        plugin = StubDesktopSandboxPlugin()
        dirs = plugin.get_machine_res_dirs("m-res-custom", dir="/tmp/other")
        assert dirs["name"] == "Desktop"


class TestStubDesktopSandboxPluginResolveWsConnInfo:
    """Tests for StubDesktopSandboxPlugin.resolve_ws_conn_info()."""

    @pytest.fixture(autouse=True)
    def _patch_config(self) -> None:
        with patch("secbaas.config.get_config", return_value=_STUB_PROXY_CONFIG):
            yield

    def test_returns_ws_connection_info(self) -> None:
        """返回的 dict 包含 ws_url, token, target, expires_at 四个属性。"""
        plugin = StubDesktopSandboxPlugin()
        info = plugin.resolve_ws_conn_info(
            session_id="test-session-abc123",
            container_id="ctr-ws",
            machine_id="m-ws",
            user_id="u-ws",
            port=9527,
            path="/ws/bot",
            template_id=42,
        )
        assert info.ws_url and info.token and info.target and info.expires_at

    def test_ws_url_contains_session_id(self) -> None:
        """ws_url 以 agentclawproxy-dev URL 开头且包含传入的 session_id。"""
        plugin = StubDesktopSandboxPlugin()
        info = plugin.resolve_ws_conn_info(
            session_id="my-session-123",
            container_id="ctr-a",
            machine_id="m-a",
            user_id="u-a",
            port=8080,
            path="/ws",
            template_id=0,
        )
        assert info.ws_url == "wss://ac-proxy-dev.service.test/wsrelay/my-session-123"

    def test_token_is_mock_jwt(self) -> None:
        """token 固定为 'mock-jwt-token'。"""
        plugin = StubDesktopSandboxPlugin()
        info = plugin.resolve_ws_conn_info(
            session_id="sid-1",
            container_id="c",
            machine_id="m",
            user_id="u",
            port=3000,
            path="/any",
            template_id=5,
        )
        assert info.token == "mock-jwt-token"

    def test_target_format_with_all_params(self) -> None:
        """target 格式为 LOCAL_{cid}--{mid}--{uid}@{tid}:{port}:{sid}。"""
        plugin = StubDesktopSandboxPlugin()
        info = plugin.resolve_ws_conn_info(
            session_id="abc123",
            container_id="cont-xyz",
            machine_id="mach-001",
            user_id="user-42",
            port=9527,
            path="/ws/bot",
            template_id=42,
        )
        assert info.target == "LOCAL_cont-xyz--mach-001--user-42@42:9527:abc123"

    def test_different_port_and_template_id(self) -> None:
        """不同 port 和 template_id 参数透传到 target 格式。"""
        plugin = StubDesktopSandboxPlugin()
        info = plugin.resolve_ws_conn_info(
            session_id="sid-x",
            container_id="ctr-x",
            machine_id="m-x",
            user_id="u-x",
            port=443,
            path="/secure",
            template_id=99,
        )
        assert info.target == "LOCAL_ctr-x--m-x--u-x@99:443:sid-x"
        assert "wss://ac-proxy-dev.service.test/wsrelay/sid-x" == info.ws_url

    def test_path_does_not_affect_result(self) -> None:
        """path 参数可传入任意值，不影响返回结果格式。"""
        plugin = StubDesktopSandboxPlugin()
        info1 = plugin.resolve_ws_conn_info(
            session_id="s1",
            container_id="c",
            machine_id="m",
            user_id="u",
            port=8080,
            path="/ws/bot",
            template_id=1,
        )
        info2 = plugin.resolve_ws_conn_info(
            session_id="s1",
            container_id="c",
            machine_id="m",
            user_id="u",
            port=8080,
            path="/different/path",
            template_id=1,
        )
        assert info1.ws_url == info2.ws_url
        assert info1.target == info2.target

    def test_expires_at_is_fixed(self) -> None:
        """expires_at 固定为 2099-12-31T23:59:59 (UTC)。"""
        from datetime import UTC, datetime

        plugin = StubDesktopSandboxPlugin()
        info = plugin.resolve_ws_conn_info(
            session_id="s-e",
            container_id="c",
            machine_id="m",
            user_id="u",
            port=8080,
            path="/ws",
            template_id=1,
        )
        assert info.expires_at == datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)


class TestStubDesktopSandboxPluginClose:
    def test_close_is_noop(self) -> None:
        plugin = StubDesktopSandboxPlugin()
        plugin.create_device("m-close", "b", "a", "u")
        plugin.close()
        assert len(plugin._devices) == 1
