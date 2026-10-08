"""Unit tests for RealDockerSandbox and RealDockerSandboxPlugin.

Mocks docker SDK to avoid needing a real Docker daemon.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from unittest.mock import MagicMock, call, patch

import pytest

# Pre-populate sys.modules with docker mocks before importing the plugin
_docker_mock = MagicMock()
_docker_errors_mock = MagicMock()
_requests_exceptions_mock = MagicMock()
sys.modules.setdefault("docker", _docker_mock)
sys.modules.setdefault("docker.errors", _docker_errors_mock)
sys.modules.setdefault("requests.exceptions", _requests_exceptions_mock)

from secbaas.community.api.device_manage import ErrorCode, PaasError
from secbaas.community.plugins.sandbox.docker.real._real_docker_sandbox_plugin import (
    RealDockerSandbox,
    RealDockerSandboxPlugin,
    _import_docker,
)

# ==================== Fake exception hierarchy ====================


class FakeImageNotFound(Exception):
    pass


class FakeNotFound(Exception):
    pass


class FakeAPIError(Exception):
    def __init__(self, msg, response=None, explanation=""):
        super().__init__(msg)
        self.response = response or MagicMock(status_code=500)
        self.explanation = explanation

    def is_server_error(self):
        return self.response.status_code >= 500


class FakeDockerException(Exception):
    pass


class FakeReadTimeout(Exception):
    pass


class FakeConnectTimeout(Exception):
    pass


class FakeConnectionError(Exception):
    pass


def _setup_docker_errors():
    """Patch the module's docker_errors and requests_exceptions with real exception classes."""
    import secbaas.community.plugins.sandbox.docker.real._real_docker_sandbox_plugin as mod

    mod.docker_errors = _docker_errors_mock
    mod.docker_errors.ImageNotFound = FakeImageNotFound
    mod.docker_errors.NotFound = FakeNotFound
    mod.docker_errors.APIError = FakeAPIError
    mod.docker_errors.DockerException = FakeDockerException
    mod.requests_exceptions = _requests_exceptions_mock
    mod.requests_exceptions.ReadTimeout = FakeReadTimeout
    mod.requests_exceptions.ConnectTimeout = FakeConnectTimeout
    mod.requests_exceptions.ConnectionError = FakeConnectionError
    mod._docker_loaded = True


_setup_docker_errors()


# ==================== RealDockerSandbox tests ====================


def _make_container(
    status="running", container_id="abc123", image="alpine:latest", port_bindings=None
):
    container = MagicMock()
    container.id = container_id
    container.attrs = {
        "State": {"Status": status},
        "Id": container_id,
        "Config": {"Image": image},
        "HostConfig": {"PortBindings": port_bindings or {}},
    }
    return container


class TestRealDockerSandbox:
    def test_sandbox_id_property(self):
        sandbox = RealDockerSandbox("sbx-1", MagicMock(), 8080)
        assert sandbox.sandbox_id == "sbx-1"

    def test_is_ready_true(self):
        container = _make_container(status="running")
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        assert sandbox.is_ready is True

    def test_is_ready_false_not_running(self):
        container = _make_container(status="exited")
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        assert sandbox.is_ready is False

    def test_is_ready_reload_exception(self):
        container = MagicMock()
        container.reload.side_effect = Exception("daemon down")
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        assert sandbox.is_ready is False

    def test_get_info_success(self):
        pb = {"8080/tcp": [{"HostPort": "9090"}]}
        container = _make_container(port_bindings=pb)
        sandbox = RealDockerSandbox("sbx-1", container, 9090)
        info = sandbox.get_info()
        assert info["sandbox_id"] == "sbx-1"
        assert info["status"] == "running"
        assert info["host_port"] == 9090
        assert info["image"] == "alpine:latest"

    def test_get_info_reload_exception(self):
        container = MagicMock()
        container.reload.side_effect = Exception("daemon down")
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        info = sandbox.get_info()
        assert info["status"] == "unknown"
        assert info["host_port"] == 8080

    def test_get_info_no_port_bindings(self):
        container = _make_container()
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        info = sandbox.get_info()
        assert info["host_port"] == 8080

    def test_get_info_invalid_host_port(self):
        pb = {"8080/tcp": [{"HostPort": "invalid"}]}
        container = _make_container(port_bindings=pb)
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        info = sandbox.get_info()
        assert info["host_port"] == 0

    def test_exec_command_success(self):
        container = MagicMock()
        container.exec_run.return_value = (0, (b"hello", b"world"))
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        result = sandbox.exec_command("echo hello")
        assert result.exit_code == 0
        assert result.stdout == "hello"
        assert result.stderr == "world"
        assert result.elapsed_time >= 0

    def test_exec_command_none_exit_code(self):
        container = MagicMock()
        container.exec_run.return_value = (None, (b"", b""))
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        result = sandbox.exec_command("cmd")
        assert result.exit_code == -1

    def test_exec_command_none_output(self):
        container = MagicMock()
        container.exec_run.return_value = (0, (None, None))
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        result = sandbox.exec_command("cmd")
        assert result.stdout == ""
        assert result.stderr == ""

    def test_exec_command_exception(self):
        container = MagicMock()
        container.exec_run.side_effect = Exception("exec failed")
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        with pytest.raises(RuntimeError, match="exec_command failed"):
            sandbox.exec_command("cmd")

    def test_destroy_success(self):
        container = MagicMock()
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        assert sandbox.destroy() is True
        container.stop.assert_called_once_with(timeout=30)
        container.remove.assert_called_once_with(force=True)

    def test_destroy_not_found_on_stop(self):
        container = MagicMock()
        container.stop.side_effect = FakeNotFound("gone")
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        assert sandbox.destroy() is True

    def test_destroy_not_found_on_remove(self):
        container = MagicMock()
        container.remove.side_effect = FakeNotFound("gone")
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        assert sandbox.destroy() is True

    def test_destroy_api_error_on_stop(self):
        container = MagicMock()
        container.stop.side_effect = FakeAPIError("err", explanation="oops")
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        assert sandbox.destroy() is True
        container.remove.assert_called_once_with(force=True)

    def test_destroy_api_error_on_remove(self):
        container = MagicMock()
        container.remove.side_effect = FakeAPIError("err", explanation="oops")
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        assert sandbox.destroy() is True

    def test_destroy_general_exception_on_stop(self):
        container = MagicMock()
        container.stop.side_effect = RuntimeError("boom")
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        with pytest.raises(RuntimeError, match="destroy stop failed"):
            sandbox.destroy()

    def test_restart_success(self):
        container = MagicMock()
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        assert sandbox.restart() is True
        container.restart.assert_called_once_with(timeout=30)

    def test_restart_not_found(self):
        container = MagicMock()
        container.restart.side_effect = FakeNotFound("gone")
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        with pytest.raises(RuntimeError, match="not found"):
            sandbox.restart()

    def test_restart_api_error(self):
        container = MagicMock()
        container.restart.side_effect = FakeAPIError("err")
        sandbox = RealDockerSandbox("sbx-1", container, 8080)
        with pytest.raises(RuntimeError, match="restart failed"):
            sandbox.restart()


# ==================== RealDockerSandboxPlugin tests ====================


class TestRealDockerSandboxPlugin:
    @pytest.fixture
    def plugin(self):
        p = RealDockerSandboxPlugin()
        p._client = MagicMock()
        return p

    def test_get_client_caches(self, plugin):
        client1 = plugin._get_client()
        client2 = plugin._get_client()
        assert client1 is client2

    def test_get_client_init_failure(self):
        plugin = RealDockerSandboxPlugin()
        with patch.object(plugin, "_client", None):
            with patch("docker.from_env", side_effect=Exception("no docker")):
                with pytest.raises(PaasError) as exc:
                    plugin._get_client()
                assert exc.value.code == ErrorCode.PLATFORM_UNAVAILABLE

    def test_get_client_ping_failure(self):
        plugin = RealDockerSandboxPlugin()
        mock_client = MagicMock()
        mock_client.ping.side_effect = FakeAPIError("ping failed")
        with patch("docker.from_env", return_value=mock_client):
            with pytest.raises(PaasError) as exc:
                plugin._get_client()
            assert exc.value.code == ErrorCode.PLATFORM_UNAVAILABLE

    def test_parse_image_with_tag(self):
        assert RealDockerSandboxPlugin._parse_image("alpine:latest") == (
            "alpine",
            "latest",
        )

    def test_parse_image_without_tag(self):
        assert RealDockerSandboxPlugin._parse_image("nginx") == ("nginx", None)

    def test_parse_image_with_registry_port(self):
        repo, tag = RealDockerSandboxPlugin._parse_image("registry:5000/alpine:3.18")
        assert tag == "3.18"

    # --- _map_docker_error ---

    def test_map_error_image_not_found(self, plugin):
        err = plugin._map_docker_error(FakeImageNotFound("img not found"))
        assert err.code == ErrorCode.CONFIG_INVALID

    def test_map_error_not_found(self, plugin):
        err = plugin._map_docker_error(FakeNotFound("not found"))
        assert err.code == ErrorCode.DEVICE_NOT_FOUND

    def test_map_error_api_409(self, plugin):
        resp = MagicMock(status_code=409)
        err = plugin._map_docker_error(
            FakeAPIError("conflict", response=resp, explanation="name conflict")
        )
        assert err.code == ErrorCode.DEVICE_ALREADY_EXISTS

    def test_map_error_api_500_oci(self, plugin):
        resp = MagicMock(status_code=500)
        err = plugin._map_docker_error(
            FakeAPIError("err", response=resp, explanation="OCI runtime error")
        )
        assert err.code == ErrorCode.DEVICE_CREATION_FAILED

    def test_map_error_api_500(self, plugin):
        resp = MagicMock(status_code=500)
        err = plugin._map_docker_error(
            FakeAPIError("err", response=resp, explanation="daemon error")
        )
        assert err.code == ErrorCode.PLATFORM_ERROR

    def test_map_error_api_400(self, plugin):
        resp = MagicMock(status_code=400)
        err = plugin._map_docker_error(
            FakeAPIError("err", response=resp, explanation="bad request")
        )
        assert err.code == ErrorCode.CONFIG_INVALID

    def test_map_error_read_timeout(self, plugin):
        err = plugin._map_docker_error(FakeReadTimeout("timeout"))
        assert err.code == ErrorCode.PLATFORM_UNAVAILABLE

    def test_map_error_connect_timeout(self, plugin):
        err = plugin._map_docker_error(FakeConnectTimeout("timeout"))
        assert err.code == ErrorCode.PLATFORM_UNAVAILABLE

    def test_map_error_connection_error(self, plugin):
        err = plugin._map_docker_error(FakeConnectionError("conn err"))
        assert err.code == ErrorCode.PLATFORM_UNAVAILABLE

    def test_map_error_docker_exception(self, plugin):
        err = plugin._map_docker_error(FakeDockerException("docker err"))
        assert err.code == ErrorCode.PLATFORM_UNAVAILABLE

    def test_map_error_unknown_reraises(self, plugin):
        with pytest.raises(ValueError):
            plugin._map_docker_error(ValueError("unknown"))

    # --- _ensure_image_sync ---

    def test_ensure_image_always(self, plugin):
        plugin._ensure_image_sync("alpine:latest", "always")
        plugin._client.images.pull.assert_called_once_with("alpine:latest")

    def test_ensure_image_always_failure(self, plugin):
        plugin._client.images.pull.side_effect = FakeNotFound("not found")
        with pytest.raises(PaasError):
            plugin._ensure_image_sync("alpine:latest", "always")

    def test_ensure_image_never_found(self, plugin):
        plugin._client.images.get.side_effect = FakeImageNotFound("not found")
        with pytest.raises(PaasError) as exc:
            plugin._ensure_image_sync("alpine:latest", "never")
        assert exc.value.code == ErrorCode.CONFIG_INVALID

    def test_ensure_image_never_not_found(self, plugin):
        plugin._client.images.get.side_effect = FakeNotFound("not found")
        with pytest.raises(PaasError):
            plugin._ensure_image_sync("alpine:latest", "never")

    def test_ensure_image_never_other_error(self, plugin):
        plugin._client.images.get.side_effect = FakeAPIError("err")
        with pytest.raises(PaasError):
            plugin._ensure_image_sync("alpine:latest", "never")

    def test_ensure_image_if_not_present_existing(self, plugin):
        plugin._ensure_image_sync("alpine:latest", "if_not_present")
        plugin._client.images.get.assert_called_once_with("alpine:latest")
        plugin._client.images.pull.assert_not_called()

    def test_ensure_image_if_not_present_missing(self, plugin):
        plugin._client.images.get.side_effect = FakeImageNotFound("not found")
        plugin._ensure_image_sync("alpine:latest", "if_not_present")
        plugin._client.images.pull.assert_called_once_with("alpine:latest")

    def test_ensure_image_if_not_present_missing_not_found(self, plugin):
        plugin._client.images.get.side_effect = FakeNotFound("not found")
        plugin._ensure_image_sync("alpine:latest", "if_not_present")
        plugin._client.images.pull.assert_called_once_with("alpine:latest")

    def test_ensure_image_if_not_present_get_error(self, plugin):
        plugin._client.images.get.side_effect = FakeAPIError("err")
        with pytest.raises(PaasError):
            plugin._ensure_image_sync("alpine:latest", "if_not_present")

    def test_ensure_image_if_not_present_pull_error(self, plugin):
        plugin._client.images.get.side_effect = FakeImageNotFound("not found")
        plugin._client.images.pull.side_effect = FakeAPIError("pull err")
        with pytest.raises(PaasError):
            plugin._ensure_image_sync("alpine:latest", "if_not_present")

    # --- _create_container_sync ---

    def test_create_container_success(self, plugin):
        mock_container = MagicMock()
        mock_container.id = "ctr-1"
        plugin._client.containers.create.return_value = mock_container
        result = plugin._create_container_sync(
            "ctr-1", "alpine:latest", 8080, None, None, None, "tenant-1", 1
        )
        assert result is mock_container

    def test_create_container_with_cpu_memory(self, plugin):
        mock_container = MagicMock()
        plugin._client.containers.create.return_value = mock_container
        plugin._create_container_sync(
            "ctr-1", "alpine:latest", 8080, {"K": "V"}, "2.0", "512m", "tenant-1", 1
        )
        kwargs = plugin._client.containers.create.call_args[1]
        assert kwargs["nano_cpus"] == 2000000000
        assert kwargs["mem_limit"] == "512m"

    def test_create_container_invalid_cpu(self, plugin):
        mock_container = MagicMock()
        plugin._client.containers.create.return_value = mock_container
        plugin._create_container_sync(
            "ctr-1", "alpine:latest", 8080, None, "invalid", None, "tenant-1", 1
        )
        kwargs = plugin._client.containers.create.call_args[1]
        assert "nano_cpus" not in kwargs

    def test_create_container_failure(self, plugin):
        plugin._client.containers.create.side_effect = FakeAPIError("err")
        with pytest.raises(PaasError):
            plugin._create_container_sync(
                "ctr-1", "alpine:latest", 8080, None, None, None, "tenant-1", 1
            )

    # --- _extract_host_port_sync ---

    def test_extract_host_port_success(self, plugin):
        container = MagicMock()
        container.attrs = {
            "NetworkSettings": {"Ports": {"8080/tcp": [{"HostPort": "9090"}]}}
        }
        assert plugin._extract_host_port_sync(container, 8080) == 9090

    def test_extract_host_port_no_binding(self, plugin):
        container = MagicMock()
        container.attrs = {"NetworkSettings": {"Ports": {}}}
        with pytest.raises(PaasError) as exc:
            plugin._extract_host_port_sync(container, 8080)
        assert exc.value.code == ErrorCode.DEVICE_UNAVAILABLE

    # --- _poll_health ---

    def test_poll_health_success(self, plugin):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.get", return_value=mock_resp):
            with patch.object(time, "sleep"):
                plugin._poll_health(9090, "/health", 10)

    def test_poll_health_timeout(self, plugin):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("httpx.get", return_value=mock_resp):
            with patch.object(time, "monotonic", side_effect=[0, 0, 100, 100]):
                with patch.object(time, "sleep"):
                    with pytest.raises(PaasError) as exc:
                        plugin._poll_health(9090, "/health", 10)
                    assert exc.value.code == ErrorCode.DEVICE_NOT_READY

    def test_poll_health_connection_error_then_success(self, plugin):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        call_count = {"n": 0}

        def fake_get(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ConnectionError("refused")
            return mock_resp

        with patch("httpx.get", side_effect=fake_get):
            with patch.object(time, "sleep"):
                plugin._poll_health(9090, "/health", 10)

    # --- create_device ---

    def test_create_device_success(self, plugin):
        mock_container = MagicMock()
        mock_container.id = "ctr-123"
        mock_container.attrs = {
            "NetworkSettings": {"Ports": {"8080/tcp": [{"HostPort": "9090"}]}},
            "HostConfig": {"PortBindings": {"8080/tcp": [{"HostPort": "9090"}]}},
        }
        plugin._client.containers.create.return_value = mock_container

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.get", return_value=mock_resp), patch.object(time, "sleep"):
            sandbox = plugin.create_device(
                template_id=1,
                template_uuid="uuid-1",
                tenant_name="tenant-1",
                container_name="ctr-1",
                image="alpine:latest",
                container_port=8080,
            )
        assert sandbox.sandbox_id == "ctr-123"

    def test_create_device_start_failure_cleanup(self, plugin):
        mock_container = MagicMock()
        mock_container.id = "ctr-123"
        plugin._client.containers.create.return_value = mock_container
        mock_container.start.side_effect = FakeAPIError("start failed")

        with pytest.raises(PaasError):
            plugin.create_device(
                template_id=1,
                template_uuid="uuid-1",
                tenant_name="tenant-1",
                container_name="ctr-1",
                image="alpine:latest",
                container_port=8080,
            )
        mock_container.remove.assert_called_once_with(force=True)

    # --- destroy_device ---

    def test_destroy_device_success(self, plugin):
        container = MagicMock()
        plugin._client.containers.get.return_value = container
        assert plugin.destroy_device("ctr-1") is True
        container.stop.assert_called_once_with(timeout=30)
        container.remove.assert_called_once_with(force=True)

    def test_destroy_device_not_found(self, plugin):
        plugin._client.containers.get.side_effect = FakeNotFound("gone")
        assert plugin.destroy_device("ctr-1") is True

    def test_destroy_device_stop_not_found(self, plugin):
        container = MagicMock()
        container.stop.side_effect = FakeNotFound("gone")
        plugin._client.containers.get.return_value = container
        assert plugin.destroy_device("ctr-1") is True

    def test_destroy_device_api_error_on_stop(self, plugin):
        container = MagicMock()
        container.stop.side_effect = FakeAPIError("err")
        plugin._client.containers.get.return_value = container
        assert plugin.destroy_device("ctr-1") is True
        container.remove.assert_called_once_with(force=True)

    def test_destroy_device_remove_not_found(self, plugin):
        container = MagicMock()
        container.remove.side_effect = FakeNotFound("gone")
        plugin._client.containers.get.return_value = container
        assert plugin.destroy_device("ctr-1") is True

    def test_destroy_device_unexpected_error_on_stop(self, plugin):
        container = MagicMock()
        container.stop.side_effect = RuntimeError("boom")
        plugin._client.containers.get.return_value = container
        assert plugin.destroy_device("ctr-1") is True

    def test_destroy_device_unexpected_error_on_remove(self, plugin):
        container = MagicMock()
        container.remove.side_effect = RuntimeError("boom")
        plugin._client.containers.get.return_value = container
        assert plugin.destroy_device("ctr-1") is True

    # --- connect_device ---

    def test_connect_device_success(self, plugin):
        container = MagicMock()
        container.attrs = {
            "HostConfig": {"PortBindings": {"8080/tcp": [{"HostPort": "9090"}]}}
        }
        plugin._client.containers.get.return_value = container
        sandbox = plugin.connect_device("ctr-1")
        assert sandbox.sandbox_id == "ctr-1"
        assert sandbox._host_port == 9090

    def test_connect_device_no_port_bindings(self, plugin):
        container = MagicMock()
        container.attrs = {"HostConfig": {"PortBindings": {}}}
        plugin._client.containers.get.return_value = container
        sandbox = plugin.connect_device("ctr-1")
        assert sandbox._host_port == 0

    def test_connect_device_not_found(self, plugin):
        plugin._client.containers.get.side_effect = FakeNotFound("gone")
        with pytest.raises(PaasError) as exc:
            plugin.connect_device("ctr-1")
        assert exc.value.code == ErrorCode.DEVICE_NOT_FOUND

    def test_connect_device_other_error(self, plugin):
        plugin._client.containers.get.side_effect = FakeAPIError("err")
        with pytest.raises(PaasError):
            plugin.connect_device("ctr-1")

    # --- resolve_ws_conn_info ---

    def test_resolve_ws_conn_info(self, plugin):
        result = plugin.resolve_ws_conn_info("dev-1", 9090, "/api/ws")
        assert "ws://127.0.0.1:9090/api/ws" in result.ws_url
        assert result.target == "DOCKER_dev-1:9090"

    def test_resolve_ws_conn_info_normalizes_path(self):
        plugin = RealDockerSandboxPlugin()
        result = plugin.resolve_ws_conn_info("dev-1", 9090, "ws/path")
        assert result.ws_url == "ws://127.0.0.1:9090/ws/path"

    # --- resolve_invoke_http_info ---

    def test_resolve_invoke_http_info(self, plugin):
        result = plugin.resolve_invoke_http_info("dev-1", 9090, "/api/health")
        assert "http://127.0.0.1:9090/api/health" in result.http_url

    def test_resolve_invoke_http_info_normalizes_path(self):
        plugin = RealDockerSandboxPlugin()
        result = plugin.resolve_invoke_http_info("dev-1", 9090, "health")
        assert result.http_url == "http://127.0.0.1:9090/health"

    # --- invoke_http_in_device ---

    def test_invoke_http_in_device_success(self, plugin):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.content = b'{"ok": true}'
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = mock_response
            mock_client_cls.return_value = mock_client
            result = plugin.invoke_http_in_device("dev-1", "GET", 9090, "/api/health")
        assert result["status_code"] == 200
        import base64

        assert base64.b64decode(result["body"]) == b'{"ok": true}'

    def test_invoke_http_in_device_with_query(self, plugin):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.content = b""
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = mock_response
            mock_client_cls.return_value = mock_client
            result = plugin.invoke_http_in_device(
                "dev-1", "GET", 9090, "/api", query_string="?foo=bar"
            )
        assert result["status_code"] == 200

    def test_invoke_http_in_device_httpx_error(self, plugin):
        import httpx

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.request.side_effect = httpx.HTTPError("conn refused")
            mock_client_cls.return_value = mock_client
            with pytest.raises(PaasError) as exc:
                plugin.invoke_http_in_device("dev-1", "GET", 9090, "/api")
            assert exc.value.code == ErrorCode.PLATFORM_UNAVAILABLE

    # --- close ---

    def test_close(self, plugin):
        plugin.close()  # no-op, should not raise
