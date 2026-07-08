"""Tests for MockInstanceRouter.

Per Architecture Rule 21: Every Protocol must have Mock implementation.
These tests validate the MockInstanceRouter behavior.
"""

import pytest

from secbaas.core.service.paas.desktop.instance_router._exceptions import (
    ForwardHTTPError,
    ForwardTimeoutError,
)
from secbaas.core.service.paas.desktop.instance_router._mock_instance_router import (
    MockInstanceRouter,
)


class TestMockInstanceRouter:
    """Tests for MockInstanceRouter."""

    @pytest.fixture
    def router(self) -> MockInstanceRouter:
        """Create a MockInstanceRouter."""
        return MockInstanceRouter()

    def test_init(self, router: MockInstanceRouter) -> None:
        """Test initialization creates empty mocks."""
        assert router._instance_mappings == {}
        assert router._route_responses == {}
        assert router._route_errors == {}
        assert router._calls == []

    def test_mock_get_instance_for(self, router: MockInstanceRouter) -> None:
        """Test mocking get_instance_for."""
        router.mock_get_instance_for("machine-1", "dev", "instance-a")

        result = router.get_instance_for("machine-1", "dev")

        assert result == "instance-a"

    def test_mock_get_instance_for_none(self, router: MockInstanceRouter) -> None:
        """Test mocking get_instance_for with None."""
        router.mock_get_instance_for("machine-1", "dev", None)

        result = router.get_instance_for("machine-1", "dev")

        assert result is None

    def test_get_instance_for_records_call(self, router: MockInstanceRouter) -> None:
        """Test that get_instance_for records its call."""
        router.mock_get_instance_for("machine-1", "dev", "instance-a")

        router.get_instance_for("machine-1", "dev")

        calls = router.get_calls()
        assert len(calls) == 1
        assert calls[0]["method"] == "get_instance_for"
        assert calls[0]["machine_id"] == "machine-1"
        assert calls[0]["env"] == "dev"

    def test_get_instance_for_unknown(self, router: MockInstanceRouter) -> None:
        """Test get_instance_for returns None for unknown machine."""
        result = router.get_instance_for("unknown", "dev")

        assert result is None

    @pytest.mark.asyncio
    async def test_mock_route_response(self, router: MockInstanceRouter) -> None:
        """Test mocking route_to_instance success."""
        response = {"status": "success", "data": {"output": "hello"}}
        router.mock_route_response("instance-a", "execute_command", response)

        result = await router.route_to_instance(
            target_instance="instance-a",
            action="execute_command",
            machine_id="machine-1",
            params={},
            request_id="req-123",
        )

        assert result == response

    @pytest.mark.asyncio
    async def test_mock_route_error(self, router: MockInstanceRouter) -> None:
        """Test mocking route_to_instance error."""
        error = ForwardTimeoutError("instance-a", "execute_command", 30.0)
        router.mock_route_error("instance-a", "execute_command", error)

        with pytest.raises(ForwardTimeoutError):
            await router.route_to_instance(
                target_instance="instance-a",
                action="execute_command",
                machine_id="machine-1",
                params={},
                request_id="req-123",
            )

    @pytest.mark.asyncio
    async def test_route_to_instance_records_call(
        self, router: MockInstanceRouter
    ) -> None:
        """Test that route_to_instance records its call."""
        router.mock_route_response(
            "instance-a", "execute_command", {"status": "success"}
        )

        await router.route_to_instance(
            target_instance="instance-a",
            action="execute_command",
            machine_id="machine-1",
            params={},
            request_id="req-123",
        )

        calls = router.get_calls()
        assert len(calls) == 1
        assert calls[0]["method"] == "route_to_instance"
        assert calls[0]["target_instance"] == "instance-a"
        assert calls[0]["action"] == "execute_command"
        assert calls[0]["machine_id"] == "machine-1"
        assert calls[0]["params"] == {}
        assert calls[0]["request_id"] == "req-123"

    @pytest.mark.asyncio
    async def test_route_to_instance_unconfigured(
        self, router: MockInstanceRouter
    ) -> None:
        """Test route_to_instance raises RuntimeError when unconfigured."""
        with pytest.raises(RuntimeError, match="No mock configured"):
            await router.route_to_instance(
                target_instance="instance-a",
                action="execute_command",
                machine_id="machine-1",
                params={},
                request_id="req-123",
            )

    def test_clear_calls(self, router: MockInstanceRouter) -> None:
        """Test clear_calls resets call history."""
        router.mock_get_instance_for("machine-1", "dev", "instance-a")
        router.get_instance_for("machine-1", "dev")

        assert len(router.get_calls()) == 1

        router.clear_calls()

        assert len(router.get_calls()) == 0

    def test_reset(self, router: MockInstanceRouter) -> None:
        """Test reset clears everything."""
        router.mock_get_instance_for("machine-1", "dev", "instance-a")
        router.mock_route_response(
            "instance-a", "execute_command", {"status": "success"}
        )
        router.mock_route_error("instance-b", "execute_command", RuntimeError("error"))
        router.get_instance_for("machine-1", "dev")

        router.reset()

        assert router._instance_mappings == {}
        assert router._route_responses == {}
        assert router._route_errors == {}
        assert router._calls == []

    @pytest.mark.asyncio
    async def test_mock_route_error_http_error(
        self, router: MockInstanceRouter
    ) -> None:
        """Test mocking HTTP error response."""
        error = ForwardHTTPError("instance-a", 503, "Service Unavailable")
        router.mock_route_error("instance-a", "execute_command", error)

        with pytest.raises(ForwardHTTPError) as exc_info:
            await router.route_to_instance(
                target_instance="instance-a",
                action="execute_command",
                machine_id="machine-1",
                params={},
                request_id="req-123",
            )

        assert exc_info.value.status_code == 503
        assert exc_info.value.response_body == "Service Unavailable"
