"""MockInstanceRouter implementation.

Per Microkernel Architecture Rule 21: Every Protocol must have Mock implementation.
Used for unit testing to control InstanceRouter behavior without actual HTTP calls.
"""


class MockInstanceRouter:
    """Mock InstanceRouter for unit testing.

    Allows pre-configured responses and tracks all calls for verification.
    Use this in tests to avoid actual HTTP requests while maintaining
    behavioral verification.

    Example:
        ```python
        router = MockInstanceRouter()
        router.mock_get_instance_for("machine-1", "dev", "instance-A")
        router.mock_route_response(
            "instance-A", "execute_command",
            {"status": "success", "data": {"output": "hello"}}
        )

        result = router.get_instance_for("machine-1", "dev")
        assert result == "instance-A"

        response = await router.route_to_instance(
            target_instance="instance-A",
            action="execute_command",
            machine_id="machine-1",
            params={"container_id": "abc123"},
            request_id="req-123",
        )
        assert response["status"] == "success"
        ```
    """

    def __init__(self) -> None:
        """Initialize MockInstanceRouter with empty mocks."""
        self._instance_mappings: dict[tuple[str, str], str] = {}
        self._route_responses: dict[tuple[str, str], dict] = {}
        self._route_errors: dict[tuple[str, str], Exception] = {}
        self._calls: list[dict] = []

    def mock_get_instance_for(
        self, machine_id: str, env: str, instance: str | None
    ) -> None:
        """Configure return value for get_instance_for.

        Args:
            machine_id: The machine ID to configure.
            env: The environment to configure.
            instance: The instance to return, or None.
        """
        self._instance_mappings[(machine_id, env)] = instance

    def mock_route_response(
        self, target_instance: str, action: str, response: dict
    ) -> None:
        """Configure successful response for route_to_instance.

        Args:
            target_instance: The target instance to configure.
            action: The action to configure.
            response: The response dict to return.
        """
        self._route_responses[(target_instance, action)] = response
        # Clear any error mock for this target/action
        self._route_errors.pop((target_instance, action), None)

    def mock_route_error(
        self, target_instance: str, action: str, error: Exception
    ) -> None:
        """Configure error for route_to_instance.

        Args:
            target_instance: The target instance to configure.
            action: The action to configure.
            error: The exception to raise.

        Examples:
            ```python
            # Simulate timeout
            router.mock_route_error(
                "instance-A", "execute_command",
                ForwardTimeoutError("instance-A", "execute_command", 30.0)
            )

            # Simulate HTTP error
            router.mock_route_error(
                "instance-A", "execute_command",
                ForwardHTTPError("instance-A", 503, "Service Unavailable")
            )
            ```
        """
        self._route_errors[(target_instance, action)] = error
        # Clear any success mock for this target/action
        self._route_responses.pop((target_instance, action), None)

    def get_calls(self) -> list[dict]:
        """Get all recorded calls.

        Returns:
            List of call records, each with method name and arguments.
        """
        return self._calls.copy()

    def clear_calls(self) -> None:
        """Clear all recorded calls."""
        self._calls = []

    def reset(self) -> None:
        """Reset all mocks and calls."""
        self._instance_mappings = {}
        self._route_responses = {}
        self._route_errors = {}
        self._calls = []

    def get_instance_for(self, machine_id: str, env: str) -> str | None:
        """Get mocked instance for machine.

        Args:
            machine_id: The machine identifier.
            env: The environment.

        Returns:
            Pre-configured instance or None if not configured.

        Raises:
            InstanceNotFoundError: If explicitly configured to raise.
        """
        self._calls.append(
            {
                "method": "get_instance_for",
                "machine_id": machine_id,
                "env": env,
            }
        )

        # Check for error mock first
        error_key = (machine_id, env)
        if error_key in self._route_errors:
            raise self._route_errors[error_key]

        return self._instance_mappings.get(error_key)

    async def route_to_instance(
        self,
        target_instance: str,
        action: str,
        machine_id: str,
        params: dict,
        request_id: str,
    ) -> dict:
        """Return mocked response or raise configured error.

        Args:
            target_instance: The target instance.
            action: The action being forwarded.
            params: The action parameters.
            request_id: The request ID.

        Returns:
            Pre-configured response dict.

        Raises:
            ForwardTimeoutError: If configured.
            ForwardHTTPError: If configured.
            RuntimeError: If no mock configured for target/action.
        """
        self._calls.append(
            {
                "method": "route_to_instance",
                "target_instance": target_instance,
                "action": action,
                "machine_id": machine_id,
                "params": params,
                "request_id": request_id,
            }
        )

        key = (target_instance, action)

        # Check for error mock first
        if key in self._route_errors:
            raise self._route_errors[key]

        # Check for success mock
        if key in self._route_responses:
            return self._route_responses[key]

        # No mock configured - raise helpful error
        raise RuntimeError(
            f"No mock configured for route_to_instance("
            f"target_instance={target_instance}, action={action}). "
            f"Call mock_route_response() or mock_route_error() first."
        )
