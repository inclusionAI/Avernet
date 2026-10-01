"""Unit tests for StubK8sSandbox, StubK8sSandboxPlugin, and StubCommandResult."""

from __future__ import annotations

import pytest


class TestStubK8sSandboxImport:
    """Verify the stub module imports correctly and all three classes exist."""

    def test_import_stub_classes(self) -> None:
        from secbaas.community.plugins.sandbox.k8s.stub import (
            StubCommandResult,
            StubK8sSandbox,
            StubK8sSandboxPlugin,
        )

        assert StubK8sSandbox is not None
        assert StubCommandResult is not None
        assert StubK8sSandboxPlugin is not None


class TestStubK8sSandboxPlugin:
    """Verify StubK8sSandboxPlugin construct and basic operations."""

    def test_plugin_initialization(self) -> None:
        from secbaas.community.plugins.sandbox.k8s.stub import (
            StubK8sSandboxPlugin,
        )

        plugin = StubK8sSandboxPlugin()
        assert plugin._sandboxes == {}
        assert plugin._next_ip_index == 1

    def test_create_device(self) -> None:
        from secbaas.community.plugins.sandbox.k8s.stub import (
            StubK8sSandboxPlugin,
        )

        plugin = StubK8sSandboxPlugin()
        sandbox = plugin.create_device(
            template_id=1,
            template_uuid="tmpl-uuid",
            tenant_name="test-tenant",
            namespace="default",
            image="test-image:latest",
            cpu_request="100m",
            cpu_limit="200m",
            memory_request="128Mi",
            memory_limit="256Mi",
        )
        assert sandbox.sandbox_id.startswith("stub-K8S-")
        assert sandbox.is_ready is True
        assert len(plugin._sandboxes) == 1

    def test_connect_device_known(self) -> None:
        from secbaas.community.plugins.sandbox.k8s.stub import (
            StubK8sSandboxPlugin,
        )

        plugin = StubK8sSandboxPlugin()
        sandbox = plugin.create_device(
            template_id=1,
            template_uuid="tmpl-uuid",
            tenant_name="test-tenant",
            namespace="default",
            image="test-image:latest",
            cpu_request="100m",
            cpu_limit="200m",
            memory_request="128Mi",
            memory_limit="256Mi",
        )
        conn = plugin.connect_device(sandbox.sandbox_id, "default")
        assert conn is sandbox

    def test_connect_device_not_found(self) -> None:
        from secbaas.community.plugins.sandbox.k8s.stub import (
            StubK8sSandboxPlugin,
        )

        plugin = StubK8sSandboxPlugin()
        with pytest.raises(RuntimeError, match="Deployment not found"):
            plugin.connect_device("nonexistent", "default")

    def test_close(self) -> None:
        from secbaas.community.plugins.sandbox.k8s.stub import (
            StubK8sSandboxPlugin,
        )

        plugin = StubK8sSandboxPlugin()
        plugin.close()  # no-op, should not raise
