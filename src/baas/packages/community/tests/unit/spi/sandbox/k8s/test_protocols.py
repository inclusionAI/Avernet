"""Unit tests for K8sSandbox and K8sSandboxPlugin Protocols.

Tests cover:
- Protocol structural conformance (issubclass, methods, attributes)
- Mock class instantiation and method signatures
- Type annotation usage
- All method parameters with various argument combinations
- Edge cases (defaults, None args, keyword args)
- Public API import verification
"""

from typing import Protocol

from secbaas.spi.sandbox import K8sSandbox, K8sSandboxPlugin

# ── Mock implementations ───────────────────────────────────────────────


class MockK8sSandbox:
    """Mock implementing the K8sSandbox Protocol."""

    is_ready: bool = False
    sandbox_id: str = "k8s-deploy-001"

    def get_info(self):
        return {
            "status": "RUNNING",
            "replicas": 1,
            "conditions": [],
            "sandbox_id": self.sandbox_id,
        }

    def exec_command(self, cmd, timeout_in_millis=30000, envs=None):
        return type(
            "Result",
            (),
            {"exit_code": 0, "stdout": "", "stderr": "", "elapsed_time": 42},
        )()

    def destroy(self):
        return True

    def restart(self):
        return True

    def update(self, **kwargs):
        return True


class MockK8sSandboxPlugin:
    """Mock implementing the K8sSandboxPlugin factory Protocol."""

    def create_device(
        self,
        template_id,
        template_uuid,
        tenant_name,
        namespace,
        image,
        cpu_request,
        cpu_limit,
        memory_request,
        memory_limit,
        envs=None,
        metadata=None,
        timeout_in_millis=120000,
    ):
        return MockK8sSandbox()

    def connect_device(self, sandbox_id, namespace):
        dev = MockK8sSandbox()
        dev.sandbox_id = sandbox_id
        return dev

    def list_instances(self, namespace, label_selector=None):
        return []

    def resolve_ws_conn_info(self, paas_device_id, port, path, namespace):
        return type("WsConnInfo", (), {"url": f"ws://10.0.0.1:{port}{path}"})()

    def resolve_invoke_http_info(self, paas_device_id, port, path, namespace):
        return type("HttpInfo", (), {"url": f"http://10.0.0.1:{port}{path}"})()

    def invoke_http_in_device(
        self,
        paas_device_id,
        method,
        port,
        path,
        namespace,
        query_string=None,
        headers=None,
        body=None,
    ):
        return {
            "status_code": 200,
            "headers": {},
            "body": "",
        }

    def close(self):
        pass


# ── K8sSandbox Protocol tests ──────────────────────────────────────────


class TestK8sSandboxProtocol:
    """Verify K8sSandbox Protocol definition."""

    def test_is_protocol(self):
        """K8sSandbox must be a Protocol class."""
        assert issubclass(K8sSandbox, Protocol)

    def test_has_attributes(self):
        """Protocol must declare is_ready and sandbox_id in __annotations__."""
        from typing import get_type_hints

        hints = get_type_hints(K8sSandbox)
        assert "is_ready" in hints
        assert "sandbox_id" in hints

    def test_has_methods(self):
        """Protocol must declare all lifecycle methods."""
        methods = [
            "get_info",
            "exec_command",
            "destroy",
            "restart",
            "update",
        ]
        for method in methods:
            assert hasattr(K8sSandbox, method), f"Missing method: {method}"
            assert callable(getattr(K8sSandbox, method)), f"Not callable: {method}"

    def test_mock_structural_conformance(self):
        """A MockK8sSandbox should structurally conform to K8sSandbox.

        Protocol conformance is structural in Python — no explicit
        registration is needed. Type checkers (mypy/pyright) will
        accept any class with matching attributes and method signatures.
        """
        # Verify the mock has all required Protocol attributes
        assert hasattr(MockK8sSandbox, "is_ready")
        assert hasattr(MockK8sSandbox, "sandbox_id")
        for method in (
            "get_info",
            "exec_command",
            "destroy",
            "restart",
            "update",
        ):
            assert hasattr(MockK8sSandbox, method)
            assert callable(getattr(MockK8sSandbox, method))


class TestK8sSandboxMock:
    """Instantiate and exercise MockK8sSandbox."""

    def test_default_attributes(self):
        dev = MockK8sSandbox()
        assert dev.is_ready is False
        assert dev.sandbox_id == "k8s-deploy-001"

    def test_custom_attributes(self):
        dev = MockK8sSandbox()
        dev.is_ready = True
        dev.sandbox_id = "deploy-42"
        assert dev.is_ready is True
        assert dev.sandbox_id == "deploy-42"

    def test_get_info(self):
        dev = MockK8sSandbox()
        info = dev.get_info()
        assert info["status"] == "RUNNING"
        assert info["replicas"] == 1

    def test_destroy(self):
        dev = MockK8sSandbox()
        assert dev.destroy() is True

    def test_restart(self):
        dev = MockK8sSandbox()
        assert dev.restart() is True

    def test_exec_command_defaults(self):
        dev = MockK8sSandbox()
        result = dev.exec_command("ls")
        assert result.exit_code == 0

    def test_exec_command_with_timeout(self):
        dev = MockK8sSandbox()
        result = dev.exec_command("sleep 10", timeout_in_millis=5000)
        assert result.exit_code == 0

    def test_exec_command_with_envs(self):
        dev = MockK8sSandbox()
        result = dev.exec_command("echo hello", envs={"KEY": "val"})
        assert result.exit_code == 0

    def test_update(self):
        dev = MockK8sSandbox()
        assert dev.update(image="new-image:v2", cpu_limit="2") is True

    def test_update_no_args(self):
        dev = MockK8sSandbox()
        assert dev.update() is True


# ── K8sSandboxPlugin Protocol tests ────────────────────────────────────


class TestK8sSandboxPluginProtocol:
    """Verify K8sSandboxPlugin Protocol definition."""

    def test_is_protocol(self):
        """K8sSandboxPlugin must be a Protocol class."""
        assert issubclass(K8sSandboxPlugin, Protocol)

    def test_has_methods(self):
        """Protocol must declare all factory methods."""
        methods = [
            "create_device",
            "connect_device",
            "list_instances",
            "resolve_ws_conn_info",
            "resolve_invoke_http_info",
            "invoke_http_in_device",
            "close",
        ]
        for method in methods:
            assert hasattr(K8sSandboxPlugin, method), f"Missing method: {method}"
            assert callable(getattr(K8sSandboxPlugin, method)), (
                f"Not callable: {method}"
            )

    def test_mock_structural_conformance(self):
        """MockK8sSandboxPlugin should structurally conform to K8sSandboxPlugin."""
        for method in (
            "create_device",
            "connect_device",
            "list_instances",
            "resolve_ws_conn_info",
            "resolve_invoke_http_info",
            "invoke_http_in_device",
            "close",
        ):
            assert hasattr(MockK8sSandboxPlugin, method)
            assert callable(getattr(MockK8sSandboxPlugin, method))


class TestK8sSandboxPluginMock:
    """Instantiate and exercise MockK8sSandboxPlugin."""

    def test_create_device_minimal(self):
        plugin = MockK8sSandboxPlugin()
        dev = plugin.create_device(
            template_id=1,
            template_uuid="tmpl-uuid-001",
            tenant_name="test-tenant",
            namespace="default",
            image="bot-runtime:latest",
            cpu_request="500m",
            cpu_limit="1",
            memory_request="512Mi",
            memory_limit="1Gi",
        )
        assert isinstance(dev, MockK8sSandbox)
        assert dev.sandbox_id == "k8s-deploy-001"
        assert dev.is_ready is False

    def test_create_device_with_all_args(self):
        plugin = MockK8sSandboxPlugin()
        dev = plugin.create_device(
            template_id=1,
            template_uuid="tmpl-uuid-001",
            tenant_name="test-tenant",
            namespace="ns-1",
            image="image:v1",
            cpu_request="500m",
            cpu_limit="2",
            memory_request="1Gi",
            memory_limit="2Gi",
            envs={"VAR": "1"},
            metadata={"key": "value"},
            timeout_in_millis=60000,
        )
        assert isinstance(dev, MockK8sSandbox)

    def test_connect_device(self):
        plugin = MockK8sSandboxPlugin()
        dev = plugin.connect_device("deploy-999", namespace="default")
        assert dev.sandbox_id == "deploy-999"

    def test_close(self):
        plugin = MockK8sSandboxPlugin()
        plugin.close()  # no-op, should not raise

    def test_create_then_connect(self):
        """Full lifecycle: create a sandbox, then connect to it again."""
        plugin = MockK8sSandboxPlugin()
        created = plugin.create_device(
            template_id=1,
            template_uuid="tmpl-uuid-001",
            tenant_name="test-tenant",
            namespace="default",
            image="bot-runtime:latest",
            cpu_request="500m",
            cpu_limit="1",
            memory_request="512Mi",
            memory_limit="1Gi",
        )
        assert created.sandbox_id == "k8s-deploy-001"

        connected = plugin.connect_device(created.sandbox_id, namespace="default")
        assert connected.sandbox_id == "k8s-deploy-001"

    def test_list_instances(self):
        plugin = MockK8sSandboxPlugin()
        result = plugin.list_instances(namespace="default")
        assert isinstance(result, list)

    def test_list_instances_with_label_selector(self):
        plugin = MockK8sSandboxPlugin()
        result = plugin.list_instances(
            namespace="default", label_selector="app=bot-runtime"
        )
        assert isinstance(result, list)

    def test_resolve_ws_conn_info(self):
        plugin = MockK8sSandboxPlugin()
        info = plugin.resolve_ws_conn_info(
            paas_device_id="deploy-001",
            port=8080,
            path="/ws",
            namespace="default",
        )
        assert info.url == "ws://10.0.0.1:8080/ws"

    def test_resolve_invoke_http_info(self):
        plugin = MockK8sSandboxPlugin()
        info = plugin.resolve_invoke_http_info(
            paas_device_id="deploy-001",
            port=8080,
            path="/api/health",
            namespace="default",
        )
        assert info.url == "http://10.0.0.1:8080/api/health"

    def test_invoke_http_in_device(self):
        plugin = MockK8sSandboxPlugin()
        result = plugin.invoke_http_in_device(
            paas_device_id="deploy-001",
            method="GET",
            port=8080,
            path="/api/health",
            namespace="default",
        )
        assert result["status_code"] == 200

    def test_invoke_http_in_device_with_headers(self):
        plugin = MockK8sSandboxPlugin()
        result = plugin.invoke_http_in_device(
            paas_device_id="deploy-001",
            method="POST",
            port=8080,
            path="/api/data",
            namespace="default",
            query_string="key=val",
            headers={"Content-Type": "application/json"},
            body=b'{"data": "test"}',
        )
        assert result["status_code"] == 200


# ── Type annotation tests ──────────────────────────────────────────────


class TestTypeAnnotationUsage:
    """Verify Protocols work as type annotations."""

    def test_k8s_sandbox_is_usable_as_annotation(self):
        """K8sSandbox should be usable as a parameter type annotation."""

        def operate(device: K8sSandbox) -> bool:
            return device.is_ready and device.restart()

        dev = MockK8sSandbox()
        dev.is_ready = True
        assert operate(dev) is True

    def test_k8s_sandbox_plugin_is_usable_as_annotation(self):
        """K8sSandboxPlugin should be usable as a parameter type annotation."""

        def spin_up(plugin: K8sSandboxPlugin) -> K8sSandbox:
            return plugin.create_device(
                template_id=1,
                template_uuid="u",
                tenant_name="t",
                namespace="default",
                image="i",
                cpu_request="500m",
                cpu_limit="1",
                memory_request="512Mi",
                memory_limit="1Gi",
            )

        plugin = MockK8sSandboxPlugin()
        dev = spin_up(plugin)
        assert isinstance(dev, MockK8sSandbox)


# ── Public API import tests ────────────────────────────────────────────


class TestPublicApiImports:
    """Verify Protocols are reachable from the public secbaas.spi.sandbox namespace."""

    def test_k8s_sandbox_imports_from_public_api(self):
        from secbaas.spi.sandbox import K8sSandbox as KS

        assert KS is K8sSandbox

    def test_k8s_sandbox_plugin_imports_from_public_api(self):
        from secbaas.spi.sandbox import K8sSandboxPlugin as KSP

        assert KSP is K8sSandboxPlugin


# ── Edge case / boundary tests ─────────────────────────────────────────


class TestK8sSandboxEdgeCases:
    """Edge cases for K8sSandbox-compatible implementations."""

    def test_get_info_on_unready_device(self):
        """get_info should return valid data even if is_ready is False."""

        class UnreadyDevice:
            is_ready = False
            sandbox_id = "pending-001"

            def get_info(self):
                return {"status": "PENDING", "sandbox_id": self.sandbox_id}

            def exec_command(self, cmd, timeout_in_millis=30000, envs=None):
                return type("Result", (), {"exit_code": 0})()

            def destroy(self):
                return True

            def restart(self):
                return True

            def update(self, **kwargs):
                return True

        device = UnreadyDevice()
        info = device.get_info()
        assert info["status"] == "PENDING"
        assert not device.is_ready

    def test_exec_command_result_with_all_attrs(self):
        """exec_command returns an object with exit_code, stdout, stderr, elapsed_time."""

        class RichResultDevice:
            is_ready = True
            sandbox_id = "rich-001"

            def get_info(self):
                return {"status": "RUNNING"}

            def exec_command(self, cmd, timeout_in_millis=30000, envs=None):
                result = type(
                    "Result",
                    (),
                    {
                        "exit_code": 0,
                        "stdout": "hello\n",
                        "stderr": "",
                        "elapsed_time": 123,
                    },
                )()
                return result

            def destroy(self):
                return True

            def restart(self):
                return True

            def update(self, **kwargs):
                return True

        device = RichResultDevice()
        result = device.exec_command("echo hello")
        assert result.exit_code == 0
        assert result.stdout == "hello\n"
        assert result.stderr == ""
        assert result.elapsed_time == 123

    def test_destroy_idempotent(self):
        """destroy may return False for already-destroyed deployments."""

        class IdempotentDevice:
            is_ready = False
            sandbox_id = "gone-001"
            _alive = True

            def get_info(self):
                return {"status": "RELEASED"}

            def exec_command(self, cmd, timeout_in_millis=30000, envs=None):
                return type("Result", (), {"exit_code": -1})()

            def destroy(self):
                if self._alive:
                    self._alive = False
                    return True
                return False

            def restart(self):
                return True

            def update(self, **kwargs):
                return True

        device = IdempotentDevice()
        assert device.destroy() is True
        assert device.destroy() is False

    def test_exec_command_empty_envs(self):
        dev = MockK8sSandbox()
        result = dev.exec_command("ls", envs={})
        assert result.exit_code == 0
