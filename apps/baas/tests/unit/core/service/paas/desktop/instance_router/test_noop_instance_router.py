"""Tests for NoopInstanceRouter.

Per Architecture Rule 21: Every Protocol must have Noop implementation.
These tests validate the NoopInstanceRouter raises NotImplementedError.
"""

import pytest

from secbaas.community.core.service.paas.desktop.instance_router._exceptions import (
    InstanceRouterError,
)
from secbaas.community.core.service.paas.desktop.instance_router._noop_instance_router import (
    NoopInstanceRouter,
)


class TestNoopInstanceRouter:
    """Tests for NoopInstanceRouter."""

    @pytest.fixture
    def router(self) -> NoopInstanceRouter:
        """Create a NoopInstanceRouter."""
        return NoopInstanceRouter()

    def test_init(self, router: NoopInstanceRouter) -> None:
        """Test initialization succeeds."""
        assert router is not None

    def test_get_instance_for_raises(self, router: NoopInstanceRouter) -> None:
        """Test get_instance_for raises InstanceRouterError."""
        with pytest.raises(InstanceRouterError) as exc_info:
            router.get_instance_for("machine-1", "dev")

        assert "InstanceRouter not initialized" in str(exc_info.value)
        assert "initialize_instance_router()" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_route_to_instance_raises(self, router: NoopInstanceRouter) -> None:
        """Test route_to_instance raises InstanceRouterError."""
        with pytest.raises(InstanceRouterError) as exc_info:
            await router.route_to_instance(
                target_instance="instance-b",
                action="execute_command",
                machine_id="machine-1",
                params={},
                request_id="req-123",
            )

        assert "InstanceRouter not initialized" in str(exc_info.value)
        assert "initialize_instance_router()" in str(exc_info.value)

    def test_error_inheritance(self) -> None:
        """Test that the error inherits from InstanceRouterError."""
        router = NoopInstanceRouter()

        with pytest.raises(InstanceRouterError):
            router.get_instance_for("m1", "dev")
