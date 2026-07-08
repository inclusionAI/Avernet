"""Contract tests for K8sSandboxPlugin Protocol implementations.

Covers:
- SPI-03: StubK8sSandboxPlugin conforms to K8sSandboxPlugin Protocol
- TST-01: All 12 K8sSandbox/K8sSandboxPlugin methods tested

Contract pattern: abstract base class K8sSandboxPluginContract defines
Protocol-level conformance tests. Concrete subclasses (e.g.,
TestStubK8sSandboxPlugin) inject the implementation via setup_method() and
may override or extend tests with implementation-specific assertions.
Phase 7 can add TestRealK8sSandboxPlugin by subclassing and providing
a real K8s implementation.
"""

from unittest.mock import MagicMock

import pytest

from secbaas.api.device_manage import K8sCredentials
from secbaas.plugins.sandbox.k8s import RealK8sSandboxPlugin, StubK8sSandboxPlugin
from secbaas.plugins.sandbox.k8s.real._client_manager import K8sClientManager
from secbaas.spi.sandbox.k8s import K8sSandboxPlugin


class K8sSandboxPluginContract:
    """Abstract conformance test contract for K8sSandboxPlugin implementations.

    Stub-biased contract: these tests encode stub-specific return shapes
    (e.g., \"RUNNING\", \"replicas\" keys). Subclasses for real implementations
    should not inherit this contract directly; they should define their own
    platform-specific assertions. Subclasses set self.plugin in setup_method().
    """

    plugin: K8sSandboxPlugin

    # -- helpers ----------------------------------------------------------------

    def _create_sandbox(
        self,
        namespace: str = "default",
        template_id: int = 1,
        template_uuid: str = "test-uuid",
        tenant_name: str = "test-tenant",
        image: str = "test-image:latest",
        cpu_request: str = "100m",
        cpu_limit: str = "200m",
        memory_request: str = "128Mi",
        memory_limit: str = "256Mi",
    ):
        """Create a sandbox with standard defaults so tests don't repeat 9 args."""
        return self.plugin.create_device(
            template_id=template_id,
            template_uuid=template_uuid,
            tenant_name=tenant_name,
            namespace=namespace,
            image=image,
            cpu_request=cpu_request,
            cpu_limit=cpu_limit,
            memory_request=memory_request,
            memory_limit=memory_limit,
        )

    # -- K8sSandbox method tests (5) -------------------------------------------

    def test_get_info(self) -> None:
        sandbox = self._create_sandbox()
        info = sandbox.get_info()

        assert isinstance(info, dict)
        assert info["status"] == "RUNNING"
        assert info["replicas"] == 1
        assert info["available_replicas"] == 1
        assert "pod_ip" in info
        assert isinstance(info["sandbox_id"], str)
        assert len(info["sandbox_id"]) > 0
        assert "namespace" in info

    def test_exec_command(self) -> None:
        sandbox = self._create_sandbox()
        result = sandbox.exec_command("ls -la")

        assert result.exit_code == 0
        assert result.stdout == "mock-output"
        assert result.stderr == ""
        assert result.elapsed_time == 0.0

    def test_destroy(self) -> None:
        sandbox = self._create_sandbox()
        result = sandbox.destroy()

        assert result is True

    def test_restart(self) -> None:
        sandbox = self._create_sandbox()
        result = sandbox.restart()

        assert result is True

    def test_update(self) -> None:
        sandbox = self._create_sandbox()
        result = sandbox.update(image="new-image:v2", cpu_limit="2")

        assert result is True

    # -- K8sSandboxPlugin method tests (7) -------------------------------------

    def test_create_device(self) -> None:
        sandbox = self._create_sandbox(
            template_id=1,
            template_uuid="uuid-1",
            tenant_name="test",
            namespace="default",
            image="test:latest",
            cpu_request="100m",
            cpu_limit="200m",
            memory_request="128Mi",
            memory_limit="256Mi",
        )

        assert sandbox.is_ready is True
        assert isinstance(sandbox.sandbox_id, str)
        assert len(sandbox.sandbox_id) > 0

    def test_connect_device(self) -> None:
        sandbox_obj = self._create_sandbox()
        sandbox_id = sandbox_obj.sandbox_id
        result = self.plugin.connect_device(sandbox_id, "default")

        # Identity check: same instance returned
        assert result is sandbox_obj
        assert result.sandbox_id == sandbox_id

    def test_list_instances(self) -> None:
        # Create 2 sandboxes in "default", 1 in "other"
        self._create_sandbox(namespace="default")
        self._create_sandbox(namespace="default")
        self._create_sandbox(namespace="other")

        result = self.plugin.list_instances("default")

        assert len(result) == 2
        for d in result:
            assert isinstance(d, dict)
            assert d["namespace"] == "default"

    def test_resolve_ws_conn_info(self) -> None:
        from secbaas.api.bot_runtime import WsConnectionInfo

        result = self.plugin.resolve_ws_conn_info("dev-1", 8080, "/ws", "default")

        assert isinstance(result, WsConnectionInfo)
        assert result.ws_url == "ws://localhost:8080/ws"
        assert result.token == ""
        assert result.target.startswith("K8S_")
        assert result.expires_at is not None

    def test_resolve_invoke_http_info(self) -> None:
        from secbaas.api.bot_runtime import HttpConnectionInfo

        result = self.plugin.resolve_invoke_http_info("dev-1", 9090, "/api", "default")

        assert isinstance(result, HttpConnectionInfo)
        assert result.http_url == "http://localhost:9090/api"
        assert result.token == ""

    def test_invoke_http_in_device(self) -> None:
        result = self.plugin.invoke_http_in_device(
            "dev-1", "GET", 8080, "/test", "default"
        )

        assert isinstance(result, dict)
        assert result["status_code"] == 200
        assert "headers" in result
        assert "body" in result
        assert result["body"] is not None

    def test_close(self) -> None:
        result = self.plugin.close()

        assert result is None

    # -- Error condition tests (1) ---------------------------------------------

    def test_connect_device_not_found(self) -> None:
        with pytest.raises(RuntimeError, match="Deployment not found"):
            self.plugin.connect_device("nonexistent", "default")

    # -- ConfigMap Protocol tests (1) ------------------------------------------

    def test_update_outbound_operation_rule_stores_configmap(self) -> None:
        """update_outbound_operation_rule stores envoy_yaml in the plugin."""
        if not hasattr(self.plugin, "_configmaps"):
            pytest.skip("Plugin does not support ConfigMap storage (stub-only test)")
        statefulset_name = "test-sts"
        namespace = "default"
        envoy_yaml = "admin:\n  address: {}\n"
        self.plugin.update_outbound_operation_rule(
            statefulset_name, namespace, envoy_yaml
        )
        cm_name = f"{statefulset_name}-proxy-rules"
        assert cm_name in self.plugin._configmaps
        assert self.plugin._configmaps[cm_name] == envoy_yaml


class TestStubK8sSandboxPlugin(K8sSandboxPluginContract):
    """Concrete contract test for StubK8sSandboxPlugin."""

    def setup_method(self) -> None:
        self.plugin = StubK8sSandboxPlugin()

    # -- Stub-specific assertions on top of base class tests ------------------

    def test_get_info(self) -> None:
        super().test_get_info()
        sandbox = self._create_sandbox()
        info = sandbox.get_info()
        assert info["sandbox_id"].startswith("stub-K8S-")

    def test_create_device(self) -> None:
        super().test_create_device()
        sandbox = self._create_sandbox()
        assert sandbox.sandbox_id.startswith("stub-K8S-")

    def test_pod_ip_deterministic(self) -> None:
        sandboxes = [
            self._create_sandbox(),
            self._create_sandbox(),
            self._create_sandbox(),
        ]
        pod_ips = [s.get_info()["pod_ip"] for s in sandboxes]

        assert pod_ips[0] == "10.244.0.1"
        assert pod_ips[1] == "10.244.0.2"
        assert pod_ips[2] == "10.244.0.3"

    def test_concurrent_update_conflict(self) -> None:
        sandbox = self._create_sandbox()
        sandbox._scaling_in_progress = True

        with pytest.raises(RuntimeError, match="Conflict"):
            sandbox.update(image="v2")

        # Clean up: reset the flag so the sandbox is usable afterwards
        sandbox._scaling_in_progress = False


class TestRealK8sSandboxPlugin:
    """Contract tests for RealK8sSandboxPlugin.

    These tests verify that RealK8sSandboxPlugin conforms to the
    K8sSandboxPlugin Protocol shape and error wrapping contract (D-05/D-06).
    Tests do NOT require a real K8s cluster -- they verify import paths,
    construction, method signatures, and RuntimeError wrapping behavior.

    Full K8s API integration tests require a real cluster and are done
    manually during integration testing.
    """

    def setup_method(self) -> None:
        """Initialize RealK8sSandboxPlugin with empty credentials and a fresh K8sClientManager."""
        self.credentials = K8sCredentials(
            template_id=1,
            template_uuid="test-uuid-real",
            namespace="test-ns",
            image="test-image:latest",
            cpu_request="100m",
            cpu_limit="200m",
            memory_request="128Mi",
            memory_limit="256Mi",
            # NO kubeconfig -- tests should NOT hit a real cluster
        )
        self.client_manager = K8sClientManager()
        self.plugin = RealK8sSandboxPlugin(
            credentials=self.credentials,
            client_manager=self.client_manager,
        )

    def test_plugin_construction(self) -> None:
        """Verify RealK8sSandboxPlugin can be constructed with credentials + client_manager."""
        assert self.plugin is not None
        assert hasattr(self.plugin, "_credentials")
        assert hasattr(self.plugin, "_client_manager")
        assert self.plugin._credentials is self.credentials
        assert self.plugin._client_manager is self.client_manager

    def test_plugin_methods_exist(self) -> None:
        """Verify all K8sSandboxPlugin Protocol methods are present and callable."""
        expected = [
            "create_device",
            "connect_device",
            "list_instances",
            "resolve_ws_conn_info",
            "resolve_invoke_http_info",
            "invoke_http_in_device",
            "close",
        ]
        for method in expected:
            assert hasattr(self.plugin, method), f"Missing method: {method}"
            assert callable(getattr(self.plugin, method)), f"Not callable: {method}"

    def test_close_is_noop(self) -> None:
        """close() should return None (no-op per D-07)."""
        result = self.plugin.close()
        assert result is None

    def test_create_device_without_kubeconfig(self) -> None:
        """create_device without kubeconfig should raise ValueError or RuntimeError."""
        with pytest.raises((ValueError, RuntimeError)):
            self.plugin.create_device(
                template_id=1,
                template_uuid="test-uuid",
                tenant_name="test-tenant",
                namespace="test-ns",
                image="test:latest",
                cpu_request="100m",
                cpu_limit="200m",
                memory_request="128Mi",
                memory_limit="256Mi",
            )

    def test_connect_device_without_kubeconfig(self) -> None:
        """connect_device without kubeconfig should raise ValueError or RuntimeError."""
        with pytest.raises((ValueError, RuntimeError)):
            self.plugin.connect_device("test-pod", "test-ns")

    def test_list_instances_without_kubeconfig(self) -> None:
        """list_instances without kubeconfig should raise ValueError or RuntimeError."""
        with pytest.raises((ValueError, RuntimeError)):
            self.plugin.list_instances("test-ns")

    def test_resolve_ws_without_kubeconfig(self) -> None:
        """resolve_ws_conn_info without kubeconfig should raise ValueError or RuntimeError."""
        with pytest.raises((ValueError, RuntimeError)):
            self.plugin.resolve_ws_conn_info("test-sts--0", 8080, "/ws", "test-ns")

    def test_invoke_http_without_kubeconfig(self) -> None:
        """invoke_http_in_device without kubeconfig should raise ValueError or RuntimeError."""
        with pytest.raises((ValueError, RuntimeError)):
            self.plugin.invoke_http_in_device(
                "test-sts--0", "GET", 8080, "/api", "test-ns"
            )

    def test_paas_device_id_format_invalid(self) -> None:
        """resolve_ws_conn_info with malformed paas_device_id should raise RuntimeError(422).

        The _parse_pod_name method runs BEFORE any K8s API call, so this test
        works even without a kubeconfig.
        """
        with pytest.raises(RuntimeError, match=r"\(422\)"):
            self.plugin.resolve_ws_conn_info("invalid-format", 8080, "/ws", "test-ns")

    def test_stub_import_still_works(self) -> None:
        """Sanity check: StubK8sSandboxPlugin can still be imported and used."""
        from secbaas.plugins.sandbox.k8s import StubK8sSandboxPlugin

        stub = StubK8sSandboxPlugin()
        assert stub is not None
        stub.close()

    def test_no_pod_ip_caching(self) -> None:
        """ADDR-04: resolve_ws_conn_info makes two separate API calls on consecutive invocations.

        Uses mock to count read_namespaced_pod calls -- verifies no caching occurs.
        We patch the CoreV1Api class in the real plugin's module so that
        CoreV1Api(client) returns our mock with read_namespaced_pod tracking.
        """
        from unittest.mock import patch

        mock_pod = MagicMock()
        mock_pod.status.pod_ip = "10.244.1.5"
        mock_pod.status.phase = "Running"

        mock_v1 = MagicMock()
        mock_v1.read_namespaced_pod.return_value = mock_pod

        mock_manager = MagicMock()
        mock_manager.get_or_create_client.return_value = MagicMock()

        with patch(
            "secbaas.plugins.sandbox.k8s.real._real_k8s_sandbox.CoreV1Api",
            return_value=mock_v1,
        ):
            plugin = RealK8sSandboxPlugin(
                credentials=self.credentials,
                client_manager=mock_manager,
            )

            # First call
            result1 = plugin.resolve_ws_conn_info("test-sts--0", 8080, "/ws", "test-ns")
            assert result1.ws_url == "ws://10.244.1.5:8080/ws"
            assert mock_v1.read_namespaced_pod.call_count == 1

            # Second call -- should trigger another read_namespaced_pod
            result2 = plugin.resolve_ws_conn_info("test-sts--0", 8080, "/ws", "test-ns")
            assert result2.ws_url == "ws://10.244.1.5:8080/ws"
            assert mock_v1.read_namespaced_pod.call_count == 2, (
                f"ADDR-04 violated: expected 2 calls, got {mock_v1.read_namespaced_pod.call_count}"
            )

    def test_no_pod_ip_caching_http(self) -> None:
        """ADDR-04: resolve_invoke_http_info makes separate API calls (no caching)."""
        from unittest.mock import patch

        mock_pod = MagicMock()
        mock_pod.status.pod_ip = "10.244.1.8"

        mock_v1 = MagicMock()
        mock_v1.read_namespaced_pod.return_value = mock_pod

        mock_manager = MagicMock()
        mock_manager.get_or_create_client.return_value = MagicMock()

        with patch(
            "secbaas.plugins.sandbox.k8s.real._real_k8s_sandbox.CoreV1Api",
            return_value=mock_v1,
        ):
            plugin = RealK8sSandboxPlugin(
                credentials=self.credentials,
                client_manager=mock_manager,
            )

            result1 = plugin.resolve_invoke_http_info(
                "test-sts--1", 9090, "/api", "test-ns"
            )
            assert result1.http_url == "http://10.244.1.8:9090/api"
            assert mock_v1.read_namespaced_pod.call_count == 1

            result2 = plugin.resolve_invoke_http_info(
                "test-sts--1", 9090, "/api", "test-ns"
            )
            assert mock_v1.read_namespaced_pod.call_count == 2, (
                f"ADDR-04 violated: expected 2 calls, got {mock_v1.read_namespaced_pod.call_count}"
            )

    def test_pod_ip_none_raises_503(self) -> None:
        """When Pod IP is None (pending Pod), RuntimeError(503) is raised per Pitfall 2."""
        from unittest.mock import patch

        mock_pod = MagicMock()
        mock_pod.status.pod_ip = None  # Pod IP not yet assigned

        mock_v1 = MagicMock()
        mock_v1.read_namespaced_pod.return_value = mock_pod

        mock_manager = MagicMock()
        mock_manager.get_or_create_client.return_value = MagicMock()

        with patch(
            "secbaas.plugins.sandbox.k8s.real._real_k8s_sandbox.CoreV1Api",
            return_value=mock_v1,
        ):
            plugin = RealK8sSandboxPlugin(
                credentials=self.credentials,
                client_manager=mock_manager,
            )

            with pytest.raises(RuntimeError, match=r"\(503\)"):
                plugin.resolve_ws_conn_info("test-sts--0", 8080, "/ws", "test-ns")

    def test_api_exception_wraps_to_runtime_error(self) -> None:
        """D-05/D-06: ApiException is wrapped as RuntimeError with (status) in message."""
        from unittest.mock import patch

        from kubernetes.client.rest import ApiException

        mock_v1 = MagicMock()
        mock_v1.read_namespaced_pod.side_effect = ApiException(
            status=404, reason="Not Found"
        )

        mock_manager = MagicMock()
        mock_manager.get_or_create_client.return_value = MagicMock()

        with patch(
            "secbaas.plugins.sandbox.k8s.real._real_k8s_sandbox.CoreV1Api",
            return_value=mock_v1,
        ):
            plugin = RealK8sSandboxPlugin(
                credentials=self.credentials,
                client_manager=mock_manager,
            )

            with pytest.raises(RuntimeError, match=r"\(404\)"):
                plugin.connect_device("missing-pod", "test-ns")
