"""Coverage tests for LocalDockerArcaSandbox.get_info().

Verifies the local-docker Arca sandbox returns the unified ArcaSandboxInfo model
with its backend-specific extras (container_id, is_ready).
"""

from __future__ import annotations

from secbaas.community.plugins.sandbox.arca.local_docker._sandbox import (
    LocalDockerArcaSandbox,
)
from secbaas.community.spi.sandbox.arca import ArcaSandboxInfo


class TestLocalDockerSandboxGetInfo:
    def test_returns_unified_sandbox_info_with_extras(self) -> None:
        sandbox = LocalDockerArcaSandbox("sb-docker-1", "tpl-1", container_id="cid-1")
        info = sandbox.get_info()

        assert isinstance(info, ArcaSandboxInfo)
        assert info.sandbox_id == "sb-docker-1"
        assert info.template_id == "tpl-1"
        assert info.status == "PENDING"
        assert info.container_id == "cid-1"
        assert info.is_ready is False

    def test_after_mark_ready_status_is_running(self) -> None:
        sandbox = LocalDockerArcaSandbox("sb-docker-2", "tpl-1")
        sandbox.mark_ready("cid-2")

        info = sandbox.get_info()

        assert info.container_id == "cid-2"
        assert info.is_ready is True
        assert info.status == "RUNNING"

    def test_unified_attribute_surface(self) -> None:
        sandbox = LocalDockerArcaSandbox("sb-docker-3", "tpl-1")
        info = sandbox.get_info()

        for attr in (
            "sandbox_id",
            "status",
            "template_id",
            "resources",
            "ttl_in_minutes",
            "ttl_timestamp",
            "envs",
            "snapshot_id",
            "metadata",
            "outbound_operation_rule",
            "storage",
        ):
            assert hasattr(info, attr), f"missing attribute: {attr}"

    def test_destroy_marks_status(self) -> None:
        sandbox = LocalDockerArcaSandbox("sb-docker-4", "tpl-1")
        assert sandbox.destroy() is True
        assert sandbox.get_info().status == "DESTROYED"
        assert sandbox.get_info().is_ready is False
