"""Protocol conformance tests for DesktopSandbox and DesktopSandboxPlugin.

Verifies structural subtyping, method signatures, return type correctness,
and import from the public API (secbaas.spi.sandbox).
"""

from __future__ import annotations

from typing import Any, Protocol, get_type_hints

import pytest

from secbaas.spi.sandbox import DesktopSandbox, DesktopSandboxPlugin

# ── Mock implementations ──────────────────────────────────────────────


class MockDesktopSandbox:
    """Structural subtype of DesktopSandbox with real attribute storage."""

    container_id: str
    machine_id: str
    user_id: str
    is_running: bool

    def __init__(
        self,
        container_id: str = "mock-cid",
        machine_id: str = "mock-mid",
        user_id: str = "mock-uid",
        is_running: bool = True,
    ) -> None:
        self.container_id = container_id
        self.machine_id = machine_id
        self.user_id = user_id
        self.is_running = is_running
        self._destroyed = False
        self._restarted = False

    def get_info(self) -> Any:
        """Return container info dict."""
        return {
            "container_id": self.container_id,
            "machine_id": self.machine_id,
            "user_id": self.user_id,
            "status": "running" if self.is_running else "stopped",
            "platform": "desktop",
            "port": 8080,
        }

    def exec_shell(
        self,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> Any:
        """Simulate shell execution and return a result dict."""
        return {
            "exit_code": 0,
            "stdout": f"mock: {cmd}",
            "stderr": "",
            "execution_time_ms": 12,
        }

    def http_proxy(
        self,
        method: str,
        port: int,
        path: str,
        headers: dict[str, str],
        body: bytes,
        query_string: str | None = None,
    ) -> dict[str, Any]:
        """Simulate HTTP proxy and return structured response."""
        return {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "body": "eyJzdGF0dXMiOiAib2sifQ==",  # base64: {"status": "ok"}
        }

    def destroy(self) -> bool:
        """Simulate destroy; idempotent."""
        was_running = not self._destroyed
        self._destroyed = True
        self.is_running = False
        return was_running

    def restart(self) -> bool:
        """Simulate restart."""
        self._restarted = True
        return True


class MockDesktopSandboxPlugin:
    """Structural subtype of DesktopSandboxPlugin with factory behavior."""

    def __init__(self) -> None:
        self._devices: dict[str, MockDesktopSandbox] = {}
        self._closed = False
        self._create_count = 0

    def create_device(
        self,
        machine_id: str,
        bot_uuid: str,
        agent_code: str,
        user_id: str,
        envs: dict[str, str] | None = None,
        mount_path: str | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> DesktopSandbox:
        """Create and track a mock device."""
        self._create_count += 1
        cid = f"mock-cid-{self._create_count}"
        device = MockDesktopSandbox(
            container_id=cid,
            machine_id=machine_id,
            user_id=user_id,
        )
        self._devices[cid] = device
        return device

    def connect_device(
        self, container_id: str, machine_id: str, user_id: str
    ) -> DesktopSandbox:
        """Connect to an existing device or raise."""
        device = self._devices.get(container_id)
        if device is None:
            raise RuntimeError(f"Device {container_id} not found")
        return device

    def get_machine_info(self, machine_id: str) -> dict[str, Any]:
        """Return mock machine resource info."""
        return {
            "cpu_cores": 8,
            "memory_gb": 16,
            "disk_gb": 256,
            "machine_id": machine_id,
        }

    def get_machine_res_dirs(
        self, machine_id: str, dir: str = "~/Desktop"
    ) -> dict[str, Any]:
        """Return mock directory tree."""
        return {
            "name": dir,
            "children": [
                {"name": "project_a", "children": []},
                {"name": "notes.txt"},
            ],
        }

    def resolve_ws_conn_info(
        self,
        session_id: str,
        container_id: str,
        machine_id: str,
        user_id: str,
        port: int,
        path: str,
        template_id: int,
    ) -> Any:
        """Simulate WebSocket connection info resolution via Plugin layer.

        Matches DesktopSandboxPlugin.resolve_ws_conn_info() protocol signature
        (per D-02 mixed mode — 7 params, Plugin-layer pure communication).
        """
        return {
            "ws_url": f"wss://agentclawproxy-dev.alipay.com/wsrelay/{session_id}",
            "token": "mock-jwt-token",
            "target": (
                f"LOCAL_{container_id}--{machine_id}--{user_id}"
                f"@{template_id}:{port}:{session_id}"
            ),
            "expires_at": "2099-12-31T23:59:59",
        }

    def close(self) -> None:
        """Release resources."""
        self._closed = True

    @property
    def create_count(self) -> int:
        return self._create_count

    @property
    def is_closed(self) -> bool:
        return self._closed


# ── Protocol declaration tests ────────────────────────────────────────


class TestDesktopSandboxProtocol:
    """Structural conformance and signature tests for DesktopSandbox."""

    def test_is_protocol(self) -> None:
        """THEN DesktopSandbox is a Protocol class."""
        assert issubclass(DesktopSandbox, Protocol)

    def test_all_attributes_defined(self) -> None:
        """THEN DesktopSandbox defines all expected attributes."""
        expected = {
            "container_id",
            "machine_id",
            "user_id",
            "is_running",
        }
        annotations = getattr(DesktopSandbox, "__annotations__", {})
        assert set(annotations.keys()) == expected

    def test_all_methods_defined(self) -> None:
        """THEN DesktopSandbox Protocol defines all expected methods."""
        expected = {
            "get_info",
            "exec_shell",
            "http_proxy",
            "destroy",
            "restart",
        }
        methods = {
            name
            for name, value in DesktopSandbox.__dict__.items()
            if not name.startswith("_") and callable(value)
        }
        assert methods == expected

    def test_get_info_return_type(self) -> None:
        """THEN get_info return type is Any."""
        hints = get_type_hints(DesktopSandbox.get_info)
        assert hints["return"] is Any

    def test_exec_shell_signature(self) -> None:
        """THEN exec_shell has correct parameter types and defaults."""
        hints = get_type_hints(DesktopSandbox.exec_shell)
        assert hints["cmd"] is str
        assert hints["return"] is Any

        # Check defaults via the function object
        import inspect

        sig = inspect.signature(DesktopSandbox.exec_shell)
        params = sig.parameters
        assert params["cmd"].default is inspect.Parameter.empty
        assert params["timeout_seconds"].default == 30

    def test_http_proxy_return_type(self) -> None:
        """THEN http_proxy returns dict[str, Any]."""
        hints = get_type_hints(DesktopSandbox.http_proxy)
        assert hints["return"] == dict[str, Any]

    def test_destroy_return_type(self) -> None:
        """THEN destroy returns bool."""
        hints = get_type_hints(DesktopSandbox.destroy)
        assert hints["return"] is bool

    def test_restart_return_type(self) -> None:
        """THEN restart returns bool."""
        hints = get_type_hints(DesktopSandbox.restart)
        assert hints["return"] is bool


class TestDesktopSandboxPluginProtocol:
    """Structural conformance and signature tests for DesktopSandboxPlugin."""

    def test_is_protocol(self) -> None:
        """THEN DesktopSandboxPlugin is a Protocol class."""
        assert issubclass(DesktopSandboxPlugin, Protocol)

    def test_all_methods_defined(self) -> None:
        """THEN DesktopSandboxPlugin Protocol defines all expected methods."""
        expected = {
            "create_device",
            "connect_device",
            "get_machine_info",
            "get_machine_res_dirs",
            "resolve_ws_conn_info",
            "close",
        }
        methods = {
            name
            for name, value in DesktopSandboxPlugin.__dict__.items()
            if not name.startswith("_") and callable(value)
        }
        assert methods == expected

    def test_create_device_return_type(self) -> None:
        """THEN create_device returns DesktopSandbox."""
        hints = get_type_hints(DesktopSandboxPlugin.create_device)
        assert hints["return"] is DesktopSandbox

    def test_connect_device_return_type(self) -> None:
        """THEN connect_device returns DesktopSandbox."""
        hints = get_type_hints(DesktopSandboxPlugin.connect_device)
        assert hints["return"] is DesktopSandbox

    def test_get_machine_info_return_type(self) -> None:
        """THEN get_machine_info returns dict[str, Any]."""
        hints = get_type_hints(DesktopSandboxPlugin.get_machine_info)
        assert hints["return"] == dict[str, Any]

    def test_get_machine_res_dirs_return_type(self) -> None:
        """THEN get_machine_res_dirs returns dict[str, Any]."""
        hints = get_type_hints(DesktopSandboxPlugin.get_machine_res_dirs)
        assert hints["return"] == dict[str, Any]

    def test_close_return_type(self) -> None:
        """THEN close returns None."""
        hints = get_type_hints(DesktopSandboxPlugin.close)
        assert hints["return"] is type(None)


# ── Structural subtyping (mock → protocol) tests ─────────────────────


class TestDesktopSandboxMockConformance:
    """Verify MockDesktopSandbox structurally satisfies DesktopSandbox."""

    @pytest.fixture
    def device(self) -> MockDesktopSandbox:
        return MockDesktopSandbox()

    def test_structural_subtype(self, device: MockDesktopSandbox) -> None:
        """THEN MockDesktopSandbox can be assigned to DesktopSandbox variable."""
        dd: DesktopSandbox = device
        assert dd is device

    def test_attributes_match(self, device: MockDesktopSandbox) -> None:
        """THEN mock has all required attributes with correct types."""
        assert isinstance(device.container_id, str)
        assert isinstance(device.machine_id, str)
        assert isinstance(device.user_id, str)
        assert isinstance(device.is_running, bool)

    def test_get_info_returns_dict(self, device: MockDesktopSandbox) -> None:
        """THEN get_info returns a dict with expected keys."""
        info = device.get_info()
        assert isinstance(info, dict)
        expected_keys = {
            "container_id",
            "machine_id",
            "user_id",
            "status",
            "platform",
            "port",
        }
        assert expected_keys.issubset(info.keys())

    def test_exec_shell_default_timeout(self, device: MockDesktopSandbox) -> None:
        """THEN exec_shell works with only cmd argument."""
        result = device.exec_shell("echo hello")
        assert result["exit_code"] == 0
        assert "echo hello" in result["stdout"]

    def test_exec_shell_with_env_only(self, device: MockDesktopSandbox) -> None:
        """THEN exec_shell works with cmd + env arguments."""
        result = device.exec_shell("echo $VAR", env={"VAR": "testval"})
        assert isinstance(result["exit_code"], int)
        assert result["execution_time_ms"] > 0

    def test_exec_shell_with_custom_timeout(self, device: MockDesktopSandbox) -> None:
        """THEN exec_shell accepts custom timeout."""
        result = device.exec_shell("sleep 1", timeout_seconds=10)
        assert result["exit_code"] == 0

    def test_exec_shell_all_params(self, device: MockDesktopSandbox) -> None:
        """THEN exec_shell works with all arguments."""
        result = device.exec_shell(
            "ls -la",
            env={"HOME": "/root"},
            timeout_seconds=60,
        )
        assert result["exit_code"] == 0
        assert result["stderr"] == ""

    def test_http_proxy_full_args(self, device: MockDesktopSandbox) -> None:
        """THEN http_proxy returns expected response struct."""
        result = device.http_proxy(
            method="POST",
            port=8080,
            path="/api/v1/test",
            headers={"Authorization": "Bearer xyz"},
            body=b'{"key": "value"}',
            query_string="page=1&size=10",
        )
        assert isinstance(result, dict)
        assert "status_code" in result
        assert "headers" in result
        assert "body" in result
        assert isinstance(result["status_code"], int)

    def test_http_proxy_without_query_string(self, device: MockDesktopSandbox) -> None:
        """THEN http_proxy works without optional query_string."""
        result = device.http_proxy(
            method="GET",
            port=3000,
            path="/health",
            headers={},
            body=b"",
        )
        assert result["status_code"] == 200

    def test_destroy_returns_bool(self, device: MockDesktopSandbox) -> None:
        """THEN destroy returns bool and marks device as not running."""
        result = device.destroy()
        assert result is True
        assert device.is_running is False

    def test_destroy_idempotent(self, device: MockDesktopSandbox) -> None:
        """THEN destroy is idempotent (second call returns expected value)."""
        first = device.destroy()
        second = device.destroy()
        assert isinstance(first, bool)
        assert isinstance(second, bool)
        assert device.is_running is False

    def test_restart_returns_bool(self, device: MockDesktopSandbox) -> None:
        """THEN restart returns bool."""
        result = device.restart()
        assert result is True


class TestDesktopSandboxPluginMockConformance:
    """Verify MockDesktopSandboxPlugin structurally satisfies DesktopSandboxPlugin."""

    @pytest.fixture
    def plugin(self) -> MockDesktopSandboxPlugin:
        return MockDesktopSandboxPlugin()

    def test_structural_subtype(self, plugin: MockDesktopSandboxPlugin) -> None:
        """THEN MockDesktopSandboxPlugin can be assigned to DesktopSandboxPlugin variable."""
        dp: DesktopSandboxPlugin = plugin
        assert dp is plugin

    def test_create_device_returns_desktop_device(
        self, plugin: MockDesktopSandboxPlugin
    ) -> None:
        """THEN create_device returns a DesktopSandbox with correct attributes."""
        device = plugin.create_device(
            machine_id="machine-1",
            bot_uuid="bot-uuid-1",
            agent_code="agent-001",
            user_id="user-1",
        )
        assert isinstance(device.container_id, str)
        assert isinstance(device.machine_id, str)
        assert isinstance(device.user_id, str)
        assert isinstance(device.is_running, bool)

    def test_create_device_minimal_args(self, plugin: MockDesktopSandboxPlugin) -> None:
        """THEN create_device works with only required arguments."""
        device = plugin.create_device(
            machine_id="m1",
            bot_uuid="b1",
            agent_code="a1",
            user_id="u1",
        )
        assert device.machine_id == "m1"

    def test_create_device_with_all_optional_args(
        self, plugin: MockDesktopSandboxPlugin
    ) -> None:
        """THEN create_device accepts all optional parameters."""
        device = plugin.create_device(
            machine_id="m1",
            bot_uuid="b1",
            agent_code="a1",
            user_id="u1",
            envs={"NODE_ENV": "production"},
            mount_path="/mnt/data",
            name="test-container",
            description="A test container",
        )
        assert device.machine_id == "m1"
        assert device.is_running is True

    def test_connect_device_existing(self, plugin: MockDesktopSandboxPlugin) -> None:
        """GIVEN a created device, THEN connect_device returns it."""
        created = plugin.create_device("m1", "b1", "a1", "u1")
        connected = plugin.connect_device(created.container_id, "m1", "u1")
        assert connected.container_id == created.container_id

    def test_connect_device_nonexistent(self, plugin: MockDesktopSandboxPlugin) -> None:
        """THEN connect_device raises RuntimeError for unknown device."""
        with pytest.raises(RuntimeError, match="not found"):
            plugin.connect_device("nonexistent", "m1", "u1")

    def test_get_machine_info_returns_expected_keys(
        self, plugin: MockDesktopSandboxPlugin
    ) -> None:
        """THEN get_machine_info returns dict with resource keys."""
        info = plugin.get_machine_info("machine-1")
        assert isinstance(info, dict)
        assert "cpu_cores" in info
        assert "memory_gb" in info
        assert "disk_gb" in info
        assert info["machine_id"] == "machine-1"

    def test_get_machine_res_dirs_default_dir(
        self, plugin: MockDesktopSandboxPlugin
    ) -> None:
        """THEN get_machine_res_dirs uses default dir."""
        tree = plugin.get_machine_res_dirs("machine-1")
        assert isinstance(tree, dict)
        assert "name" in tree
        assert "children" in tree

    def test_get_machine_res_dirs_custom_dir(
        self, plugin: MockDesktopSandboxPlugin
    ) -> None:
        """THEN get_machine_res_dirs accepts custom dir."""
        tree = plugin.get_machine_res_dirs("machine-1", dir="/opt/app")
        assert tree["name"] == "/opt/app"

    def test_resolve_ws_conn_info_returns_expected_keys(
        self, plugin: MockDesktopSandboxPlugin
    ) -> None:
        """THEN resolve_ws_conn_info returns dict with ws_url, token, target, expires_at."""
        info = plugin.resolve_ws_conn_info(
            session_id="test-sid-001",
            container_id="ctr-ws",
            machine_id="m-ws",
            user_id="u-ws",
            port=9222,
            path="/ws/debug",
            template_id=42,
        )
        assert isinstance(info, dict)
        assert "ws_url" in info
        assert "token" in info
        assert "target" in info
        assert "expires_at" in info
        assert "test-sid-001" in info["ws_url"]
        assert "m-ws" in info["target"]

    def test_close(self, plugin: MockDesktopSandboxPlugin) -> None:
        """THEN close marks plugin as closed."""
        plugin.close()
        assert plugin.is_closed is True

    def test_close_return_type_is_none(self, plugin: MockDesktopSandboxPlugin) -> None:
        """THEN close() returns None."""
        result = plugin.close()
        assert result is None


# ── Import from public API tests ──────────────────────────────────────


class TestPublicApiExports:
    """Verify DesktopSandbox and DesktopSandboxPlugin are exported from secbaas.spi.sandbox."""

    def test_desktop_device_importable(self) -> None:
        """THEN DesktopSandbox is importable from secbaas.spi.sandbox."""
        from secbaas.spi.sandbox import DesktopSandbox as DD

        assert DD is DesktopSandbox

    def test_desktop_sandbox_plugin_importable(self) -> None:
        """THEN DesktopSandboxPlugin is importable from secbaas.spi.sandbox."""
        from secbaas.spi.sandbox import DesktopSandboxPlugin as DDP

        assert DDP is DesktopSandboxPlugin

    def test_both_in_all(self) -> None:
        """THEN both classes are listed in __all__."""
        from secbaas.spi.sandbox import __all__ as exported

        assert "DesktopSandbox" in exported
        assert "DesktopSandboxPlugin" in exported


# ── Edge case tests ───────────────────────────────────────────────────


class TestDesktopSandboxEdgeCases:
    """Edge case behavior for DesktopSandbox mock."""

    def test_initial_running_state(self) -> None:
        """THEN a fresh MockDesktopSandbox has is_running=True by default."""
        d = MockDesktopSandbox()
        assert d.is_running is True

    def test_custom_initial_state(self) -> None:
        """THEN is_running can be set via constructor."""
        d = MockDesktopSandbox(is_running=False)
        assert d.is_running is False
        assert d.get_info()["status"] == "stopped"

    def test_destroy_then_get_info(self) -> None:
        """THEN get_info after destroy shows stopped status."""
        d = MockDesktopSandbox()
        d.destroy()
        assert d.get_info()["status"] == "stopped"

    def test_exec_shell_result_structure(self) -> None:
        """THEN exec_shell result has exact expected shape."""
        d = MockDesktopSandbox()
        result = d.exec_shell("uptime")
        assert set(result.keys()) == {
            "exit_code",
            "stdout",
            "stderr",
            "execution_time_ms",
        }

    def test_http_proxy_body_is_bytes_input(self) -> None:
        """THEN http_proxy accepts body as bytes and returns str body."""
        d = MockDesktopSandbox()
        result = d.http_proxy(
            method="POST",
            port=9000,
            path="/endpoint",
            headers={},
            body=b"raw bytes",
        )
        assert isinstance(result["body"], str)


class TestDesktopSandboxPluginEdgeCases:
    """Edge case behavior for DesktopSandboxPlugin mock."""

    def test_create_device_increments_count(self) -> None:
        """THEN creating multiple devices yields unique container_ids."""
        p = MockDesktopSandboxPlugin()
        d1 = p.create_device("m1", "b1", "a1", "u1")
        d2 = p.create_device("m2", "b2", "a2", "u2")
        assert p.create_count == 2
        assert d1.container_id != d2.container_id

    def test_connect_device_with_wrong_machine_id(self) -> None:
        """THEN connect_device still resolves by container_id only."""
        p = MockDesktopSandboxPlugin()
        created = p.create_device("correct-mid", "b1", "a1", "u1")
        connected = p.connect_device(created.container_id, "wrong-mid", "u1")
        assert connected.container_id == created.container_id

    def test_get_machine_info_different_machines(self) -> None:
        """THEN get_machine_info reflects the requested machine_id."""
        p = MockDesktopSandboxPlugin()
        info1 = p.get_machine_info("machine-a")
        info2 = p.get_machine_info("machine-b")
        assert info1["machine_id"] == "machine-a"
        assert info2["machine_id"] == "machine-b"

    def test_close_idempotent(self) -> None:
        """THEN calling close twice is safe."""
        p = MockDesktopSandboxPlugin()
        p.close()
        p.close()
        assert p.is_closed is True
