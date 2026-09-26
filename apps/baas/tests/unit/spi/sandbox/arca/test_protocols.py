"""Unit tests for ArcaSandbox and ArcaSandboxPlugin Protocols.

Tests cover:
- Protocol structural conformance (issubclass, methods, attributes)
- Mock class instantiation and method signatures
- Type annotation usage
- All method parameters with various argument combinations
- Edge cases (defaults, None args, keyword args)
"""

from typing import Protocol

from secbaas.community.spi.sandbox import ArcaSandbox, ArcaSandboxPlugin

# ── Mock implementations ───────────────────────────────────────────────


class MockArcaSandbox:
    """Mock implementing the ArcaSandbox Protocol."""

    is_ready: bool = False
    sandbox_id: str = "mock-000"

    def get_info(self):
        return {
            "status": "RUNNING",
            "template_id": "tmpl-1",
            "sandbox_id": self.sandbox_id,
        }

    def destroy(self):
        return True

    def exec_command(self, cmd, timeout_in_millis=30000, envs=None):
        return type(
            "Result",
            (),
            {"exit_code": 0, "stdout": "", "stderr": "", "elapsed_time": 42},
        )()

    def update_outbound_rule(self, rule, mode):
        return True

    def extend_ttl(self, ttl_minutes):
        return True


class MockArcaSandboxPlugin:
    """Mock implementing the ArcaSandboxPlugin factory Protocol."""

    def create_sync_sandbox(
        self,
        template_id,
        ttl_in_minutes=None,
        envs=None,
        mount_points=None,
        resource_spec=None,
        metadata=None,
        outbound_operation_rule=None,
        storage=None,
        timeout_in_millis=60000,
        ready_timeout_in_seconds=60,
    ):
        return MockArcaSandbox()

    def connect_sync_sandbox(self, sandbox_id):
        dev = MockArcaSandbox()
        dev.sandbox_id = sandbox_id
        return dev

    def close(self):
        pass


# ── ArcaSandbox Protocol tests ──────────────────────────────────────────


class TestArcaSandboxProtocol:
    """Verify ArcaSandbox Protocol definition."""

    def test_is_protocol(self):
        """ArcaSandbox must be a Protocol class."""
        assert issubclass(ArcaSandbox, Protocol)

    def test_has_attributes(self):
        """Protocol must declare is_ready and sandbox_id in __annotations__."""
        from typing import get_type_hints

        hints = get_type_hints(ArcaSandbox)
        assert "is_ready" in hints
        assert "sandbox_id" in hints

    def test_has_methods(self):
        """Protocol must declare all lifecycle methods."""
        methods = [
            "get_info",
            "destroy",
            "exec_command",
            "update_outbound_rule",
            "extend_ttl",
        ]
        for method in methods:
            assert hasattr(ArcaSandbox, method), f"Missing method: {method}"
            assert callable(getattr(ArcaSandbox, method)), f"Not callable: {method}"

    def test_mock_structural_conformance(self):
        """A MockArcaSandbox should structurally conform to ArcaSandbox.

        Protocol conformance is structural in Python — no explicit
        registration is needed. Type checkers (mypy/pyright) will
        accept any class with matching attributes and method signatures.
        """
        # Verify the mock has all required Protocol attributes
        assert hasattr(MockArcaSandbox, "is_ready")
        assert hasattr(MockArcaSandbox, "sandbox_id")
        for method in (
            "get_info",
            "destroy",
            "exec_command",
            "update_outbound_rule",
            "extend_ttl",
        ):
            assert hasattr(MockArcaSandbox, method)
            assert callable(getattr(MockArcaSandbox, method))


class TestArcaSandboxMock:
    """Instantiate and exercise MockArcaSandbox."""

    def test_default_attributes(self):
        dev = MockArcaSandbox()
        assert dev.is_ready is False
        assert dev.sandbox_id == "mock-000"

    def test_custom_attributes(self):
        dev = MockArcaSandbox()
        dev.is_ready = True
        dev.sandbox_id = "sandbox-42"
        assert dev.is_ready is True
        assert dev.sandbox_id == "sandbox-42"

    def test_get_info(self):
        dev = MockArcaSandbox()
        info = dev.get_info()
        assert info["status"] == "RUNNING"
        assert info["template_id"] == "tmpl-1"

    def test_destroy(self):
        dev = MockArcaSandbox()
        assert dev.destroy() is True

    def test_exec_command_defaults(self):
        dev = MockArcaSandbox()
        result = dev.exec_command("ls")
        assert result.exit_code == 0

    def test_exec_command_with_timeout(self):
        dev = MockArcaSandbox()
        result = dev.exec_command("sleep 10", timeout_in_millis=5000)
        assert result.exit_code == 0

    def test_exec_command_with_envs(self):
        dev = MockArcaSandbox()
        result = dev.exec_command("echo hello", envs={"KEY": "val"})
        assert result.exit_code == 0

    def test_exec_command_keyword_args(self):
        dev = MockArcaSandbox()
        result = dev.exec_command(cmd="echo x", timeout_in_millis=10000, envs=None)
        assert result.exit_code == 0

    def test_update_outbound_rule(self):
        dev = MockArcaSandbox()
        # rule and mode can be any objects — test with dict and string
        assert dev.update_outbound_rule({"ip": "0.0.0.0/0"}, "REPLACE") is True

    def test_extend_ttl(self):
        dev = MockArcaSandbox()
        assert dev.extend_ttl(30) is True

    def test_extend_ttl_large_value(self):
        dev = MockArcaSandbox()
        assert dev.extend_ttl(99999) is True


# ── ArcaSandboxPlugin Protocol tests ────────────────────────────────────


class TestArcaSandboxPluginProtocol:
    """Verify ArcaSandboxPlugin Protocol definition."""

    def test_is_protocol(self):
        """ArcaSandboxPlugin must be a Protocol class."""
        assert issubclass(ArcaSandboxPlugin, Protocol)

    def test_has_methods(self):
        """Protocol must declare all factory methods."""
        methods = ["create_sync_sandbox", "connect_sync_sandbox", "close"]
        for method in methods:
            assert hasattr(ArcaSandboxPlugin, method), f"Missing method: {method}"
            assert callable(getattr(ArcaSandboxPlugin, method)), (
                f"Not callable: {method}"
            )

    def test_mock_structural_conformance(self):
        """MockArcaSandboxPlugin should structurally conform to ArcaSandboxPlugin."""
        for method in ("create_sync_sandbox", "connect_sync_sandbox", "close"):
            assert hasattr(MockArcaSandboxPlugin, method)
            assert callable(getattr(MockArcaSandboxPlugin, method))


class TestArcaSandboxPluginMock:
    """Instantiate and exercise MockArcaSandboxPlugin."""

    def test_create_sandbox_minimal(self):
        plugin = MockArcaSandboxPlugin()
        dev = plugin.create_sync_sandbox("template-x")
        assert isinstance(dev, MockArcaSandbox)
        assert dev.sandbox_id == "mock-000"
        assert dev.is_ready is False

    def test_create_sandbox_with_all_args(self):
        plugin = MockArcaSandboxPlugin()
        dev = plugin.create_sync_sandbox(
            template_id="tmpl-1",
            ttl_in_minutes=120,
            envs={"VAR": "1"},
            mount_points=[{"path": "/data"}],
            resource_spec={"cpu": 2, "mem": 4096},
            metadata={"key": "value"},
            outbound_operation_rule={"allow": ["*"]},
            storage={"nas": "nas-001"},
            timeout_in_millis=30000,
            ready_timeout_in_seconds=90,
        )
        assert isinstance(dev, MockArcaSandbox)

    def test_create_sandbox_with_ttl_none(self):
        plugin = MockArcaSandboxPlugin()
        dev = plugin.create_sync_sandbox("tmpl-1", ttl_in_minutes=None)
        assert isinstance(dev, MockArcaSandbox)

    def test_connect_sandbox(self):
        plugin = MockArcaSandboxPlugin()
        dev = plugin.connect_sync_sandbox("sandbox-999")
        assert dev.sandbox_id == "sandbox-999"

    def test_close(self):
        plugin = MockArcaSandboxPlugin()
        plugin.close()  # no-op, should not raise

    def test_create_then_connect(self):
        """Full lifecycle: create a sandbox, then connect to it again."""
        plugin = MockArcaSandboxPlugin()
        created = plugin.create_sync_sandbox("tmpl-1")
        assert created.sandbox_id == "mock-000"

        connected = plugin.connect_sync_sandbox(created.sandbox_id)
        assert connected.sandbox_id == "mock-000"


# ── Type annotation tests ──────────────────────────────────────────────


class TestTypeAnnotationUsage:
    """Verify Protocols work as type annotations."""

    def test_arca_device_is_usable_as_annotation(self):
        """ArcaSandbox should be usable as a parameter type annotation."""

        def operate(device: ArcaSandbox) -> bool:
            return device.is_ready and device.extend_ttl(10)

        dev = MockArcaSandbox()
        dev.is_ready = True
        assert operate(dev) is True

    def test_arca_sandbox_plugin_is_usable_as_annotation(self):
        """ArcaSandboxPlugin should be usable as a parameter type annotation."""

        def spin_up(plugin: ArcaSandboxPlugin, template: str) -> ArcaSandbox:
            return plugin.create_sync_sandbox(template)

        plugin = MockArcaSandboxPlugin()
        dev = spin_up(plugin, "tmpl-1")
        assert isinstance(dev, MockArcaSandbox)


# ── Public API import tests ────────────────────────────────────────────


class TestPublicApiImports:
    """Verify Protocols are reachable from the public secbaas.spi.sandbox namespace."""

    def test_arca_device_imports_from_public_api(self):
        from secbaas.community.spi.sandbox import ArcaSandbox as AD

        assert AD is ArcaSandbox

    def test_arca_sandbox_plugin_imports_from_public_api(self):
        from secbaas.community.spi.sandbox import ArcaSandboxPlugin as ADP

        assert ADP is ArcaSandboxPlugin


# ── Edge case / boundary tests ─────────────────────────────────────────


class TestArcaSandboxEdgeCases:
    """Edge cases for ArcaSandbox-compatible implementations."""

    def test_get_info_on_unready_device(self):
        """get_info should return valid data even if is_ready is False."""

        class UnreadyDevice:
            is_ready = False
            sandbox_id = "pending-001"

            def get_info(self):
                return {"status": "PENDING", "sandbox_id": self.sandbox_id}

            def destroy(self):
                return True

            def exec_command(self, cmd, timeout_in_millis=30000, envs=None):
                return type("Result", (), {"exit_code": 0})()

            def update_outbound_rule(self, rule, mode):
                return True

            def extend_ttl(self, ttl_minutes):
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

            def destroy(self):
                return True

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

            def update_outbound_rule(self, rule, mode):
                return True

            def extend_ttl(self, ttl_minutes):
                return True

        device = RichResultDevice()
        result = device.exec_command("echo hello")
        assert result.exit_code == 0
        assert result.stdout == "hello\n"
        assert result.stderr == ""
        assert result.elapsed_time == 123

    def test_destroy_returns_falsy_when_already_destroyed(self):
        """destroy may return False for idempotent calls on already-destroyed device."""

        class IdempotentDevice:
            is_ready = False
            sandbox_id = "gone-001"
            _alive = True

            def get_info(self):
                return {"status": "RELEASED"}

            def destroy(self):
                if self._alive:
                    self._alive = False
                    return True
                return False

            def exec_command(self, cmd, timeout_in_millis=30000, envs=None):
                return type("Result", (), {"exit_code": -1})()

            def update_outbound_rule(self, rule, mode):
                return True

            def extend_ttl(self, ttl_minutes):
                return True

        device = IdempotentDevice()
        assert device.destroy() is True
        assert device.destroy() is False

    def test_extend_ttl_with_zero(self):
        dev = MockArcaSandbox()
        assert dev.extend_ttl(0) is True

    def test_exec_command_empty_envs(self):
        dev = MockArcaSandbox()
        result = dev.exec_command("ls", envs={})
        assert result.exit_code == 0
