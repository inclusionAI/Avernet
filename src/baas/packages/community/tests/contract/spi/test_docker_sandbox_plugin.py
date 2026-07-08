"""Contract tests for DockerSandboxPlugin Protocol implementations.

Covers:
- SPI-03: StubDockerSandboxPlugin conforms to DockerSandboxPlugin Protocol
- All 7 DockerSandboxPlugin methods + 4 DockerSandbox methods tested

Contract pattern: abstract base class DockerSandboxPluginContract defines
Protocol-level conformance tests. Concrete subclasses (e.g.,
TestStubDockerSandboxPlugin) inject the implementation via setup_method().
Future: TestRealDockerSandboxPlugin can subclass to test against real Docker daemon.
"""

import pytest

from secbaas.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.plugins.sandbox.docker.stub import StubDockerSandboxPlugin
from secbaas.spi.sandbox.docker import DockerSandboxPlugin


class DockerSandboxPluginContract:
    """Abstract conformance test contract for DockerSandboxPlugin implementations.

    Stub-biased contract: tests against StubDockerSandboxPlugin.
    Subclasses for real implementations should define their own platform-specific
    assertions. Subclasses set self.plugin in setup_method().
    """

    plugin: DockerSandboxPlugin

    # -- helpers ----------------------------------------------------------------

    def _create_sandbox(
        self,
        template_id: int = 1,
        template_uuid: str = "test-uuid",
        tenant_name: str = "test-tenant",
        container_name: str = "test-container-001",
        image: str = "alpine:latest",
        container_port: int = 8080,
    ):
        """Create a sandbox with standard defaults so tests don't repeat 6+ args."""
        return self.plugin.create_device(
            template_id=template_id,
            template_uuid=template_uuid,
            tenant_name=tenant_name,
            container_name=container_name,
            image=image,
            container_port=container_port,
        )

    # -- DockerSandbox method tests (6) -----------------------------------------

    def test_get_info(self) -> None:
        """Protocol-level: get_info() returns a dict with required keys.

        Stub-specific: status, container_id, host_port, image values
        verified in the subclass override.
        """
        sandbox = self._create_sandbox()
        info = sandbox.get_info()

        assert isinstance(info, dict)
        assert isinstance(info["sandbox_id"], str)
        assert len(info["sandbox_id"]) > 0

    def test_exec_command(self) -> None:
        """Protocol-level: exec_command() returns a result with exit_code attribute.

        Stub-specific: stdout, stderr, elapsed_time values verified
        in the subclass override.
        """
        sandbox = self._create_sandbox()
        result = sandbox.exec_command("echo hello")

        assert result is not None
        assert hasattr(result, "exit_code")
        assert hasattr(result, "stdout")
        assert hasattr(result, "stderr")
        assert hasattr(result, "elapsed_time")

    def test_destroy(self) -> None:
        """Protocol-level: destroy() returns True and marks sandbox as not ready."""
        sandbox = self._create_sandbox()
        result = sandbox.destroy()

        assert result is True

    def test_restart(self) -> None:
        """Protocol-level: restart() returns True when sandbox is alive.

        Stub-specific: restart after destroy raises RuntimeError.
        """
        sandbox = self._create_sandbox()
        result = sandbox.restart()

        assert result is True

    def test_is_ready_property(self) -> None:
        """Protocol-level: is_ready is True after creation, False after destroy."""
        sandbox = self._create_sandbox()
        assert sandbox.is_ready is True

        sandbox.destroy()
        assert sandbox.is_ready is False

    def test_sandbox_id_property(self) -> None:
        """Protocol-level: sandbox_id is a non-empty str, stable across calls."""
        sandbox = self._create_sandbox()
        sid = sandbox.sandbox_id

        assert isinstance(sid, str)
        assert len(sid) > 0
        assert sandbox.sandbox_id == sid  # stable

    # -- DockerSandboxPlugin method tests (9) -----------------------------------

    def test_create_device_returns_sandbox(self) -> None:
        """Protocol-level: create_device returns a sandbox with is_ready True."""
        sandbox = self._create_sandbox()

        assert sandbox is not None
        assert sandbox.is_ready is True
        assert sandbox.sandbox_id is not None

    def test_create_device_passes_through_params(self) -> None:
        """Protocol-level: create_device with params returns a sandbox with info."""
        sandbox = self._create_sandbox(
            template_id=42,
            container_name="specific-name",
            image="custom-image:latest",
            container_port=9090,
        )
        info = sandbox.get_info()

        assert info["sandbox_id"] is not None

    def test_destroy_device_idempotent(self) -> None:
        """Protocol-level: destroy_device is idempotent (per D-15).

        First call destroys, second call on same id succeeds,
        third call on nonexistent id also succeeds.
        """
        sandbox = self._create_sandbox()
        sandbox_id = sandbox.sandbox_id

        assert self.plugin.destroy_device(sandbox_id) is True
        assert self.plugin.destroy_device(sandbox_id) is True  # idempotent
        assert self.plugin.destroy_device("nonexistent-id") is True  # idempotent

    def test_connect_device_found(self) -> None:
        """Protocol-level: connect_device returns the same sandbox."""
        sandbox = self._create_sandbox()
        sandbox_id = sandbox.sandbox_id

        reconnected = self.plugin.connect_device(sandbox_id)

        assert reconnected.is_ready is True
        assert reconnected.sandbox_id == sandbox_id

    def test_connect_device_not_found(self) -> None:
        """Protocol-level: connect_device with nonexistent id raises RuntimeError."""
        with pytest.raises(RuntimeError):
            self.plugin.connect_device("nonexistent")

    def test_resolve_ws_conn_info(self) -> None:
        """Protocol-level: resolve_ws_conn_info returns WsConnectionInfo.

        Stub-specific: ws_url, token, target values verified in subclass override.
        """
        result = self.plugin.resolve_ws_conn_info("test-device", 8080, "/ws")

        assert isinstance(result, WsConnectionInfo)

    def test_resolve_invoke_http_info(self) -> None:
        """Protocol-level: resolve_invoke_http_info returns HttpConnectionInfo.

        Stub-specific: http_url, token values verified in subclass override.
        """
        result = self.plugin.resolve_invoke_http_info("test-device", 8080, "/api")

        assert isinstance(result, HttpConnectionInfo)

    def test_invoke_http_in_device(self) -> None:
        """Protocol-level: invoke_http_in_device returns a dict with expected keys.

        Stub-specific: status_code value verified in subclass override.
        """
        result = self.plugin.invoke_http_in_device("test-device", "GET", 8080, "/api")

        assert isinstance(result, dict)
        assert "status_code" in result
        assert "headers" in result
        assert "body" in result

    def test_close(self) -> None:
        """Protocol-level: close() does not raise."""
        self._create_sandbox()
        self.plugin.close()  # should not raise


class TestStubDockerSandboxPlugin(DockerSandboxPluginContract):
    """Stub conformance tests — runs against StubDockerSandboxPlugin."""

    def setup_method(self) -> None:
        self.plugin = StubDockerSandboxPlugin()

    # -- Stub-specific assertions (extends base class tests) --------------------

    def test_get_info(self) -> None:
        """Base check + stub-specific: status, container_id, host_port, image."""
        super().test_get_info()
        sandbox = self._create_sandbox()
        info = sandbox.get_info()
        assert info["status"] == "running"
        assert info["container_id"] is not None
        assert info["host_port"] == 18080
        assert info["image"] == "stub-image:latest"

    def test_exec_command(self) -> None:
        """Base check + stub-specific: exit_code, stdout, stderr, elapsed_time."""
        super().test_exec_command()
        sandbox = self._create_sandbox()
        result = sandbox.exec_command("echo hello")
        assert result.exit_code == 0
        assert result.stdout == "mock-output"
        assert result.stderr == ""
        assert result.elapsed_time == 0.0

    def test_destroy(self) -> None:
        """Base check + stub-specific: is_ready False, status TERMINATING."""
        super().test_destroy()
        # Create a fresh sandbox for stub-specific assertions
        sandbox2 = self._create_sandbox()
        sandbox2.destroy()
        assert sandbox2.is_ready is False
        info = sandbox2.get_info()
        assert info["status"] == "TERMINATING"

    def test_restart(self) -> None:
        """Base check + stub-specific: restart after destroy raises RuntimeError."""
        super().test_restart()
        sandbox = self._create_sandbox()
        sandbox.destroy()
        with pytest.raises(RuntimeError, match="404"):
            sandbox.restart()

    def test_resolve_ws_conn_info(self) -> None:
        """Base check + stub-specific: ws_url, token, target values."""
        super().test_resolve_ws_conn_info()
        result = self.plugin.resolve_ws_conn_info("test-device", 8080, "/ws")
        assert result.ws_url == "ws://127.0.0.1:8080/ws"
        assert result.token == ""
        assert "DOCKER_test-device:8080" in result.target

    def test_resolve_invoke_http_info(self) -> None:
        """Base check + stub-specific: http_url, token values."""
        super().test_resolve_invoke_http_info()
        result = self.plugin.resolve_invoke_http_info("test-device", 8080, "/api")
        assert result.http_url == "http://127.0.0.1:8080/api"
        assert result.token == ""

    def test_invoke_http_in_device(self) -> None:
        """Base check + stub-specific: status_code == 200."""
        super().test_invoke_http_in_device()
        result = self.plugin.invoke_http_in_device("test-device", "GET", 8080, "/api")
        assert result["status_code"] == 200

    def test_close_clears_sandboxes(self) -> None:
        """Stub-specific: close() clears sandbox dict; connect after close fails."""
        sandbox = self._create_sandbox()
        sandbox_id = sandbox.sandbox_id
        self.plugin.close()
        with pytest.raises(RuntimeError):
            self.plugin.connect_device(sandbox_id)
