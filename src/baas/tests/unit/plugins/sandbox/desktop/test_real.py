"""Unit tests for RealDesktopSandbox and RealDesktopSandboxPlugin.

Covers secbaas.plugins.sandbox.desktop._real.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.config import Config

# Pre-mock secbaas.infra modules to work around missing dependency in this branch
# (secbaas.infra was removed but arca_utils still references it at module level)
_mock_secret_utils = MagicMock()
_mock_env_utils = MagicMock()
_mock_env_utils.get_current_env = MagicMock(return_value="dev")
_mock_arca_utils = MagicMock()
_mock_arca_utils.generate_proxypass_jwt = MagicMock(return_value="mock-jwt-token")
sys.modules.setdefault("secbaas.community.infra", MagicMock())
sys.modules.setdefault("secbaas.community.infra.utils", MagicMock())
sys.modules.setdefault("secbaas.community.infra.utils.secret_utils", _mock_secret_utils)
sys.modules.setdefault("secbaas.community.infra.utils.env_utils", _mock_env_utils)
sys.modules.setdefault("secbaas.community.infra.utils.arca_utils", _mock_arca_utils)

from datetime import UTC

from secbaas.community.plugins.sandbox.desktop._real import (
    RealDesktopSandbox,
    RealDesktopSandboxPlugin,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_AGENTCLAW_PROXY_CONFIG = Config(
    user_config={
        "agentclawproxy": {
            "host": {
                "dev": "ac-proxy-dev.test",
                "pre": "ac-proxy-pre.test",
                "prod": "ac-proxy-prod.test",
            },
        },
    },
)


@pytest.fixture
def cm() -> MagicMock:
    """Create a mocked ConnectionManager."""
    return MagicMock()


@pytest.fixture
def arca_utils() -> MagicMock:
    """Create a mocked ArcaUtils instance."""
    mock = MagicMock()
    mock.generate_proxypass_jwt = MagicMock(return_value="mock-jwt-token")
    return mock


@pytest.fixture
def device(cm: MagicMock, arca_utils: MagicMock) -> RealDesktopSandbox:
    """Create a RealDesktopSandbox with default IDs."""
    return RealDesktopSandbox(
        connection_manager=cm,
        container_id="cont-abc-123",
        machine_id="mach-xyz-789",
        user_id="user-42",
        arca_utils=arca_utils,
        template_id=42,
    )


# ---------------------------------------------------------------------------
# RealDesktopSandbox --- Properties
# ---------------------------------------------------------------------------


class TestRealDesktopSandboxProperties:
    """Tests for property accessors."""

    def test_container_id(self, device: RealDesktopSandbox) -> None:
        assert device.container_id == "cont-abc-123"

    def test_machine_id(self, device: RealDesktopSandbox) -> None:
        assert device.machine_id == "mach-xyz-789"

    def test_user_id(self, device: RealDesktopSandbox) -> None:
        assert device.user_id == "user-42"


# ---------------------------------------------------------------------------
# RealDesktopSandbox --- is_running
# ---------------------------------------------------------------------------


class TestRealDesktopSandboxIsRunning:
    """Tests for the is_running property."""

    def test_is_running_true_when_status_running(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"status": "RUNNING"}}
        assert device.is_running is True

    def test_is_running_true_when_status_active(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"status": "ACTIVE"}}
        assert device.is_running is True

    def test_is_running_true_when_status_lowercase_running(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"status": "running"}}
        assert device.is_running is True

    def test_is_running_false_when_status_stopped(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"status": "STOPPED"}}
        assert device.is_running is False

    def test_is_running_false_when_status_pending(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"status": "PENDING"}}
        assert device.is_running is False

    def test_is_running_false_when_status_empty(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"status": ""}}
        assert device.is_running is False

    def test_is_running_false_when_no_status_key(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {}}
        assert device.is_running is False

    def test_is_running_false_on_exception(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.side_effect = RuntimeError("connection lost")
        assert device.is_running is False

    def test_is_running_false_on_timeout(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.side_effect = TimeoutError("timed out")
        assert device.is_running is False


# ---------------------------------------------------------------------------
# RealDesktopSandbox --- get_info
# ---------------------------------------------------------------------------


class TestRealDesktopSandboxGetInfo:
    """Tests for get_info method."""

    def test_get_info_returns_data(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {
            "data": {
                "container_id": "cont-abc-123",
                "status": "RUNNING",
                "platform": "linux",
                "port": 8080,
            }
        }
        result = device.get_info()
        assert result["container_id"] == "cont-abc-123"
        assert result["status"] == "RUNNING"
        cm.send_command.assert_called_once_with(
            "mach-xyz-789",
            {
                "action": "get_device_info",
                "params": {"container_id": "cont-abc-123"},
            },
        )

    def test_get_info_returns_full_result_when_no_data_key(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"container_id": "cont-abc-123"}
        result = device.get_info()
        assert result["container_id"] == "cont-abc-123"

    def test_get_info_raises_on_error_status(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {
            "status": "error",
            "message": "device not found",
        }
        with pytest.raises(RuntimeError, match="get_device_info failed"):
            device.get_info()


# ---------------------------------------------------------------------------
# RealDesktopSandbox --- exec_shell
# ---------------------------------------------------------------------------


class TestRealDesktopSandboxExecShell:
    """Tests for exec_shell method."""

    def test_exec_shell_basic(self, device: RealDesktopSandbox, cm: MagicMock) -> None:
        cm.send_command.return_value = {
            "data": {"exit_code": 0, "stdout": "hello", "stderr": ""}
        }
        result = device.exec_shell("echo hello")
        assert result["exit_code"] == 0
        cm.send_command.assert_called_once_with(
            "mach-xyz-789",
            {
                "action": "exec_shell",
                "params": {
                    "container_id": "cont-abc-123",
                    "cmd": "echo hello",
                    "env": {},
                    "timeout_seconds": 30,
                },
            },
        )

    def test_exec_shell_with_env(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"exit_code": 0}}
        device.exec_shell("echo $FOO", env={"FOO": "bar"})
        cm.send_command.assert_called_once_with(
            "mach-xyz-789",
            {
                "action": "exec_shell",
                "params": {
                    "container_id": "cont-abc-123",
                    "cmd": "echo $FOO",
                    "env": {"FOO": "bar"},
                    "timeout_seconds": 30,
                },
            },
        )

    def test_exec_shell_caps_timeout_at_30(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"exit_code": 0}}
        device.exec_shell("slow-cmd", timeout_seconds=120)
        args, kwargs = cm.send_command.call_args
        assert args[0] == "mach-xyz-789"
        assert args[1]["params"]["timeout_seconds"] == 30

    def test_exec_shell_respects_timeout_under_30(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"exit_code": 0}}
        device.exec_shell("quick-cmd", timeout_seconds=10)
        args, kwargs = cm.send_command.call_args
        assert args[1]["params"]["timeout_seconds"] == 10

    def test_exec_shell_default_timeout_is_30(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"exit_code": 0}}
        device.exec_shell("cmd")
        args, kwargs = cm.send_command.call_args
        assert args[1]["params"]["timeout_seconds"] == 30

    def test_exec_shell_zero_timeout(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"exit_code": 0}}
        device.exec_shell("cmd", timeout_seconds=0)
        args, kwargs = cm.send_command.call_args
        assert args[1]["params"]["timeout_seconds"] == 0

    def test_exec_shell_raises_on_error(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {
            "status": "error",
            "message": "container not running",
        }
        with pytest.raises(RuntimeError, match="exec_shell failed"):
            device.exec_shell("cmd")

    def test_exec_shell_returns_full_result_when_no_data(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"exit_code": 0, "stdout": "raw"}
        result = device.exec_shell("cmd")
        assert result["exit_code"] == 0

    def test_exec_shell_env_defaults_to_empty_dict(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"exit_code": 0}}
        device.exec_shell("cmd")
        args, kwargs = cm.send_command.call_args
        assert args[1]["params"]["env"] == {}


# ---------------------------------------------------------------------------
# RealDesktopSandbox --- http_proxy
# ---------------------------------------------------------------------------


class TestRealDesktopSandboxHttpProxy:
    """Tests for http_proxy method."""

    def test_http_proxy_success(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {
            "data": {
                "status_code": 200,
                "headers": {"Content-Type": "application/json"},
                "body": "eyJoZWxsbyI6IndvcmxkIn0=",
            }
        }
        result = device.http_proxy(
            method="GET",
            port=8080,
            path="/api/v1/health",
            headers={"Accept": "application/json"},
            body=b"",
        )
        assert result["status_code"] == 200
        cm.send_command.assert_called_once_with(
            "mach-xyz-789",
            {
                "action": "invoke_http",
                "params": {
                    "container_id": "cont-abc-123",
                    "method": "GET",
                    "port": 8080,
                    "path": "/api/v1/health",
                    "headers": {"Accept": "application/json"},
                    "body": b"",
                    "query_string": None,
                },
            },
        )

    def test_http_proxy_with_query_string(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"status_code": 200}}
        device.http_proxy(
            method="POST",
            port=3000,
            path="/api/data",
            headers={},
            body=b'{"key":"val"}',
            query_string="page=1&limit=10",
        )
        args, kwargs = cm.send_command.call_args
        params = args[1]["params"]
        assert params["method"] == "POST"
        assert params["query_string"] == "page=1&limit=10"
        assert params["body"] == b'{"key":"val"}'

    def test_http_proxy_raises_on_error(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {
            "status": "error",
            "message": "connection refused",
        }
        with pytest.raises(RuntimeError, match="invoke_http failed"):
            device.http_proxy("GET", 80, "/", {}, b"")

    def test_http_proxy_returns_full_result_when_no_data(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"status_code": 502}
        result = device.http_proxy("GET", 80, "/", {}, b"")
        assert result["status_code"] == 502

    def test_http_proxy_passes_all_headers(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"status_code": 200}}
        device.http_proxy(
            method="PUT",
            port=9090,
            path="/update",
            headers={
                "Authorization": "Bearer token",
                "X-Custom": "value",
            },
            body=b"payload",
        )
        args, kwargs = cm.send_command.call_args
        assert args[1]["params"]["headers"] == {
            "Authorization": "Bearer token",
            "X-Custom": "value",
        }


# ---------------------------------------------------------------------------
# RealDesktopSandbox --- destroy
# ---------------------------------------------------------------------------


class TestRealDesktopSandboxDestroy:
    """Tests for destroy method."""

    def test_destroy_returns_true(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"destroyed": True}}
        assert device.destroy() is True
        cm.send_command.assert_called_once_with(
            "mach-xyz-789",
            {
                "action": "destroy_device",
                "params": {"container_id": "cont-abc-123"},
            },
        )

    def test_destroy_raises_on_error(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {
            "status": "error",
            "message": "already destroyed",
        }
        with pytest.raises(RuntimeError, match="destroy_device failed"):
            device.destroy()

    def test_destroy_returns_true_with_empty_data(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {}
        assert device.destroy() is True


# ---------------------------------------------------------------------------
# RealDesktopSandbox --- restart
# ---------------------------------------------------------------------------


class TestRealDesktopSandboxRestart:
    """Tests for restart method."""

    def test_restart_returns_true(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"restarted": True}}
        assert device.restart() is True
        cm.send_command.assert_called_once_with(
            "mach-xyz-789",
            {
                "action": "restart",
                "params": {"container_id": "cont-abc-123"},
            },
        )

    def test_restart_raises_on_error(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {
            "status": "error",
            "message": "container not found",
        }
        with pytest.raises(RuntimeError, match="restart failed"):
            device.restart()

    def test_restart_returns_true_with_empty_data(
        self, device: RealDesktopSandbox, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {}
        assert device.restart() is True


# ---------------------------------------------------------------------------
# RealDesktopSandbox --- constructor variations
# ---------------------------------------------------------------------------


class TestRealDesktopSandboxConstructor:
    """Tests for constructor with various ID values."""

    def test_different_container_id(self, cm: MagicMock, arca_utils: MagicMock) -> None:
        dev = RealDesktopSandbox(cm, "c-1", "m-1", "u-1", arca_utils)
        assert dev.container_id == "c-1"

    def test_different_machine_id(self, cm: MagicMock, arca_utils: MagicMock) -> None:
        dev = RealDesktopSandbox(cm, "c-1", "m-1", "u-1", arca_utils)
        assert dev.machine_id == "m-1"

    def test_different_user_id(self, cm: MagicMock, arca_utils: MagicMock) -> None:
        dev = RealDesktopSandbox(cm, "c-1", "m-1", "u-1", arca_utils)
        assert dev.user_id == "u-1"

    def test_constructor_stores_template_id(
        self, cm: MagicMock, arca_utils: MagicMock
    ) -> None:
        dev = RealDesktopSandbox(cm, "c-1", "m-1", "u-1", arca_utils, template_id=99)
        assert dev._template_id == 99

    def test_constructor_template_id_defaults_to_zero(
        self, cm: MagicMock, arca_utils: MagicMock
    ) -> None:
        dev = RealDesktopSandbox(cm, "c-1", "m-1", "u-1", arca_utils)
        assert dev._template_id == 0


# ---------------------------------------------------------------------------
# RealDesktopSandboxPlugin --- create_device
# ---------------------------------------------------------------------------


class TestRealDesktopSandboxPluginCreateDevice:
    """Tests for plugin.create_device."""

    @pytest.fixture
    def plugin(self, cm: MagicMock, arca_utils: MagicMock) -> RealDesktopSandboxPlugin:
        return RealDesktopSandboxPlugin(connection_manager=cm, arca_utils=arca_utils)

    def test_create_device_basic(
        self, plugin: RealDesktopSandboxPlugin, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"container_id": "new-cont-123"}}
        device = plugin.create_device(
            machine_id="mach-1",
            bot_uuid="bot-uuid-abc",
            agent_code="agent-001",
            user_id="user-42",
        )
        assert isinstance(device, RealDesktopSandbox)
        assert device.container_id == "new-cont-123"
        assert device.machine_id == "mach-1"
        assert device.user_id == "user-42"
        cm.send_command.assert_called_once_with(
            "mach-1",
            {
                "action": "create_device",
                "params": {
                    "bot_id": "bot-uuid-abc",
                    "agent_code": "agent-001",
                    "user_id": "user-42",
                    "envs": {},
                    "mount_path": None,
                    "name": None,
                    "description": None,
                },
            },
        )

    def test_create_device_with_envs(
        self, plugin: RealDesktopSandboxPlugin, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"container_id": "c-env"}}
        plugin.create_device(
            machine_id="mach-1",
            bot_uuid="b-1",
            agent_code="a-1",
            user_id="u-1",
            envs={"DEBUG": "true", "PORT": "3000"},
        )
        args, kwargs = cm.send_command.call_args
        assert args[1]["params"]["envs"] == {"DEBUG": "true", "PORT": "3000"}

    def test_create_device_with_mount_path_and_name(
        self, plugin: RealDesktopSandboxPlugin, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"container_id": "c-named"}}
        plugin.create_device(
            machine_id="mach-1",
            bot_uuid="b-1",
            agent_code="a-1",
            user_id="u-1",
            mount_path="/mnt/data",
            name="my-container",
            description="Test container",
        )
        args, kwargs = cm.send_command.call_args
        params = args[1]["params"]
        assert params["mount_path"] == "/mnt/data"
        assert params["name"] == "my-container"
        assert params["description"] == "Test container"

    def test_create_device_raises_on_error(
        self, plugin: RealDesktopSandboxPlugin, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {
            "status": "error",
            "message": "machine offline",
        }
        with pytest.raises(RuntimeError, match="create_device failed"):
            plugin.create_device("m", "b", "a", "u")

    def test_create_device_uses_full_result_when_no_data(
        self, plugin: RealDesktopSandboxPlugin, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"container_id": "direct-c"}
        device = plugin.create_device("m", "b", "a", "u")
        assert device.container_id == "direct-c"

    def test_create_device_empty_container_id_fallback(
        self, plugin: RealDesktopSandboxPlugin, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {}}
        device = plugin.create_device("m", "b", "a", "u")
        assert device.container_id == ""

    def test_create_device_none_envs_defaults_to_empty(
        self, plugin: RealDesktopSandboxPlugin, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"container_id": "c"}}
        plugin.create_device("m", "b", "a", "u", envs=None)
        args, kwargs = cm.send_command.call_args
        assert args[1]["params"]["envs"] == {}


# ---------------------------------------------------------------------------
# RealDesktopSandboxPlugin --- connect_device
# ---------------------------------------------------------------------------


class TestRealDesktopSandboxPluginConnectDevice:
    """Tests for plugin.connect_device."""

    @pytest.fixture
    def plugin(self, cm: MagicMock, arca_utils: MagicMock) -> RealDesktopSandboxPlugin:
        return RealDesktopSandboxPlugin(connection_manager=cm, arca_utils=arca_utils)

    def test_connect_device_success(
        self, plugin: RealDesktopSandboxPlugin, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"status": "RUNNING"}}
        device = plugin.connect_device(
            container_id="existing-cont",
            machine_id="mach-1",
            user_id="user-42",
        )
        assert isinstance(device, RealDesktopSandbox)
        assert device.container_id == "existing-cont"
        assert device.machine_id == "mach-1"
        assert device.user_id == "user-42"
        cm.send_command.assert_called_once_with(
            "mach-1",
            {
                "action": "get_device_info",
                "params": {"container_id": "existing-cont"},
            },
        )

    def test_connect_device_raises_on_error(
        self, plugin: RealDesktopSandboxPlugin, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {
            "status": "error",
            "message": "not found",
        }
        with pytest.raises(RuntimeError, match="connect_device failed"):
            plugin.connect_device("c", "m", "u")


# ---------------------------------------------------------------------------
# RealDesktopSandboxPlugin --- get_machine_info
# ---------------------------------------------------------------------------


class TestRealDesktopSandboxPluginGetMachineInfo:
    """Tests for plugin.get_machine_info."""

    @pytest.fixture
    def plugin(self, cm: MagicMock, arca_utils: MagicMock) -> RealDesktopSandboxPlugin:
        return RealDesktopSandboxPlugin(connection_manager=cm, arca_utils=arca_utils)

    def test_get_machine_info_success(
        self, plugin: RealDesktopSandboxPlugin, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {
            "data": {
                "cpu_cores": 8,
                "memory_gb": 16,
                "disk_gb": 256,
                "os": "macOS",
            }
        }
        result = plugin.get_machine_info("mach-1")
        assert result["cpu_cores"] == 8
        assert result["os"] == "macOS"
        cm.send_command.assert_called_once_with(
            "mach-1",
            {
                "action": "get_machine_info",
                "params": {"machine_id": "mach-1"},
            },
        )

    def test_get_machine_info_raises_on_error(
        self, plugin: RealDesktopSandboxPlugin, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {
            "status": "error",
            "message": "machine unreachable",
        }
        with pytest.raises(RuntimeError, match="get_machine_info failed"):
            plugin.get_machine_info("mach-1")

    def test_get_machine_info_returns_full_result_when_no_data(
        self, plugin: RealDesktopSandboxPlugin, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"cpu_cores": 4}
        result = plugin.get_machine_info("mach-1")
        assert result["cpu_cores"] == 4


# ---------------------------------------------------------------------------
# RealDesktopSandboxPlugin --- get_machine_res_dirs
# ---------------------------------------------------------------------------


class TestRealDesktopSandboxPluginGetMachineResDirs:
    """Tests for plugin.get_machine_res_dirs."""

    @pytest.fixture
    def plugin(self, cm: MagicMock, arca_utils: MagicMock) -> RealDesktopSandboxPlugin:
        return RealDesktopSandboxPlugin(connection_manager=cm, arca_utils=arca_utils)

    def test_get_machine_res_dirs_default_dir(
        self, plugin: RealDesktopSandboxPlugin, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {
            "data": {
                "name": "Desktop",
                "children": [
                    {"name": "file.txt", "children": None},
                ],
            }
        }
        result = plugin.get_machine_res_dirs("mach-1")
        assert result["name"] == "Desktop"
        cm.send_command.assert_called_once_with(
            "mach-1",
            {
                "action": "get_machine_res_dirs",
                "params": {"machine_id": "mach-1", "dir": "~/Desktop"},
            },
        )

    def test_get_machine_res_dirs_custom_dir(
        self, plugin: RealDesktopSandboxPlugin, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"data": {"name": "Documents"}}
        plugin.get_machine_res_dirs("mach-1", dir="~/Documents")
        args, kwargs = cm.send_command.call_args
        assert args[1]["params"]["dir"] == "~/Documents"

    def test_get_machine_res_dirs_raises_on_error(
        self, plugin: RealDesktopSandboxPlugin, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {
            "status": "error",
            "message": "permission denied",
        }
        with pytest.raises(RuntimeError, match="get_machine_res_dirs failed"):
            plugin.get_machine_res_dirs("mach-1")

    def test_get_machine_res_dirs_returns_full_result_when_no_data(
        self, plugin: RealDesktopSandboxPlugin, cm: MagicMock
    ) -> None:
        cm.send_command.return_value = {"name": "root", "children": []}
        result = plugin.get_machine_res_dirs("mach-1")
        assert result["name"] == "root"


# ---------------------------------------------------------------------------
# RealDesktopSandboxPlugin --- close
# ---------------------------------------------------------------------------


class TestRealDesktopSandboxPluginClose:
    """Tests for plugin.close."""

    @pytest.fixture
    def plugin(self, cm: MagicMock, arca_utils: MagicMock) -> RealDesktopSandboxPlugin:
        return RealDesktopSandboxPlugin(connection_manager=cm, arca_utils=arca_utils)

    def test_close_is_noop(self, plugin: RealDesktopSandboxPlugin) -> None:
        # Should not raise
        plugin.close()

    def test_close_can_be_called_multiple_times(
        self, plugin: RealDesktopSandboxPlugin
    ) -> None:
        plugin.close()
        plugin.close()
        plugin.close()


# ---------------------------------------------------------------------------
# RealDesktopSandboxPlugin --- constructor
# ---------------------------------------------------------------------------


class TestRealDesktopSandboxPluginConstructor:
    """Tests for plugin constructor."""

    def test_stores_connection_manager(
        self, cm: MagicMock, arca_utils: MagicMock
    ) -> None:
        plugin = RealDesktopSandboxPlugin(connection_manager=cm, arca_utils=arca_utils)
        assert plugin._cm is cm


# ---------------------------------------------------------------------------
# Integration-style: full create → device lifecycle
# ---------------------------------------------------------------------------


class TestFullDeviceLifecycle:
    """End-to-end test through create → connect → operate → destroy."""

    def test_full_lifecycle(self, cm: MagicMock, arca_utils: MagicMock) -> None:
        plugin = RealDesktopSandboxPlugin(connection_manager=cm, arca_utils=arca_utils)

        # Create
        cm.send_command.return_value = {"data": {"container_id": "lifecycle-cont"}}
        device = plugin.create_device(
            machine_id="m-life",
            bot_uuid="bot-life",
            agent_code="agent-life",
            user_id="user-life",
        )
        assert device.container_id == "lifecycle-cont"

        # Get info
        cm.send_command.return_value = {
            "data": {"container_id": "lifecycle-cont", "status": "RUNNING"}
        }
        info = device.get_info()
        assert info["status"] == "RUNNING"

        # Is running
        assert device.is_running is True

        # Exec shell
        cm.send_command.return_value = {"data": {"exit_code": 0, "stdout": "ok"}}
        exec_result = device.exec_shell("echo ok")
        assert exec_result["exit_code"] == 0

        # Restart
        cm.send_command.return_value = {"data": {"restarted": True}}
        assert device.restart() is True

        # Destroy
        cm.send_command.return_value = {"data": {"destroyed": True}}
        assert device.destroy() is True


# ---------------------------------------------------------------------------
# RealDesktopSandboxPlugin --- resolve_ws_conn_info
# ---------------------------------------------------------------------------


class TestRealDesktopSandboxPluginResolveWsConnInfo:
    """Tests for RealDesktopSandboxPlugin.resolve_ws_conn_info()."""

    def test_returns_ws_connection_info_with_all_fields(
        self, cm: MagicMock, arca_utils: MagicMock
    ) -> None:
        """返回 WsConnectionInfo 包含 ws_url, token, target, expires_at 四个字段。"""
        from datetime import datetime, timezone

        plugin = RealDesktopSandboxPlugin(connection_manager=cm, arca_utils=arca_utils)

        with (
            patch(
                "secbaas.community.core.utils.env_utils.get_current_env",
                return_value="dev",
            ),
        ):
            result = plugin.resolve_ws_conn_info(
                session_id="test-session-abc123",
                container_id="ctr-ws",
                machine_id="m-ws",
                user_id="u-ws",
                port=9527,
                path="/ws/bot",
                template_id=42,
            )

        assert result.ws_url is not None
        assert result.ws_url != ""
        assert result.token is not None
        assert result.token != ""
        assert result.target is not None
        assert result.target != ""
        assert result.expires_at is not None
        assert result.expires_at > datetime.now(UTC)

    def test_ws_url_format_dev_env(self, cm: MagicMock, arca_utils: MagicMock) -> None:
        """ws_url 格式包含 wss://{agentclawproxy-dev-host}/wsrelay/session_id。"""

        plugin = RealDesktopSandboxPlugin(connection_manager=cm, arca_utils=arca_utils)

        with (
            patch(
                "secbaas.community.core.utils.env_utils.get_current_env",
                return_value="dev",
            ),
            patch(
                "secbaas.community.config.get_config",
                return_value=_AGENTCLAW_PROXY_CONFIG,
            ),
        ):
            result = plugin.resolve_ws_conn_info(
                session_id="my-session-123",
                container_id="ctr-a",
                machine_id="m-a",
                user_id="u-a",
                port=8080,
                path="/ws",
                template_id=0,
            )

        assert "wss://ac-proxy-dev.test/wsrelay/my-session-123" == result.ws_url

    def test_ws_url_format_pre_env(self, cm: MagicMock, arca_utils: MagicMock) -> None:
        """pre 环境 ws_url 使用 agentclawproxy-pre 域名。"""

        plugin = RealDesktopSandboxPlugin(connection_manager=cm, arca_utils=arca_utils)

        with (
            patch(
                "secbaas.community.core.utils.env_utils.get_current_env",
                return_value="pre",
            ),
            patch(
                "secbaas.community.config.get_config",
                return_value=_AGENTCLAW_PROXY_CONFIG,
            ),
        ):
            result = plugin.resolve_ws_conn_info(
                session_id="sid-pre",
                container_id="c",
                machine_id="m",
                user_id="u",
                port=3000,
                path="/any",
                template_id=5,
            )

        assert result.ws_url.startswith("wss://ac-proxy-pre.test/wsrelay/")

    def test_target_format_includes_all_ids(
        self, cm: MagicMock, arca_utils: MagicMock
    ) -> None:
        """target 格式为 LOCAL_{cid}--{mid}--{uid}@{tid}:{port}:{sid}。"""

        plugin = RealDesktopSandboxPlugin(connection_manager=cm, arca_utils=arca_utils)

        with (
            patch(
                "secbaas.community.core.utils.env_utils.get_current_env",
                return_value="dev",
            ),
        ):
            result = plugin.resolve_ws_conn_info(
                session_id="abc123",
                container_id="cont-xyz",
                machine_id="mach-001",
                user_id="user-42",
                port=9527,
                path="/ws/bot",
                template_id=42,
            )

        expected_target = "LOCAL_cont-xyz--mach-001--user-42@42:9527:abc123"
        assert result.target == expected_target

    def test_different_port_and_template_id(
        self, cm: MagicMock, arca_utils: MagicMock
    ) -> None:
        """不同 port 和 template_id 参数透传到 target。"""

        plugin = RealDesktopSandboxPlugin(connection_manager=cm, arca_utils=arca_utils)

        with (
            patch(
                "secbaas.community.core.utils.env_utils.get_current_env",
                return_value="dev",
            ),
        ):
            result = plugin.resolve_ws_conn_info(
                session_id="sid-x",
                container_id="ctr-x",
                machine_id="m-x",
                user_id="u-x",
                port=443,
                path="/secure",
                template_id=99,
            )

        expected_target = "LOCAL_ctr-x--m-x--u-x@99:443:sid-x"
        assert result.target == expected_target

    def test_session_id_in_ws_url(self, cm: MagicMock, arca_utils: MagicMock) -> None:
        """session_id 参数透传到 ws_url 路径段。"""

        plugin = RealDesktopSandboxPlugin(connection_manager=cm, arca_utils=arca_utils)

        with (
            patch(
                "secbaas.community.core.utils.env_utils.get_current_env",
                return_value="dev",
            ),
        ):
            result = plugin.resolve_ws_conn_info(
                session_id="unique-sid-777",
                container_id="c",
                machine_id="m",
                user_id="u",
                port=8080,
                path="/ws",
                template_id=1,
            )

        assert "/wsrelay/unique-sid-777" in result.ws_url
