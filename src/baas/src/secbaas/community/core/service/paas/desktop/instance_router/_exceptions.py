"""InstanceRouter custom exceptions.

Provides specific exception types for different failure modes in cross-instance
HTTP forwarding. All exceptions inherit from InstanceRouterError for easy
catch-all handling.
"""


class InstanceRouterError(Exception):
    """Base exception for InstanceRouter errors.

    All InstanceRouter-specific exceptions inherit from this class.
    """

    pass


class InstanceNotFoundError(InstanceRouterError):
    """Raised when target instance cannot be determined for a machine.

    This occurs when:
    - The machine_id is not found in the database
    - The machine exists but has no connected_server_instance
    """

    def __init__(self, machine_id: str, env: str) -> None:
        """Initialize with machine and environment details.

        Args:
            machine_id: The machine identifier that was looked up.
            env: The environment that was queried.
        """
        self.machine_id = machine_id
        self.env = env
        super().__init__(f"No instance found for machine {machine_id} in env {env}")


class ForwardTimeoutError(InstanceRouterError):
    """Raised when forward request times out.

    Indicates the target instance did not respond within the configured timeout.
    This could mean the instance is down, overloaded, or the network is partitioned.
    """

    def __init__(self, target_instance: str, action: str, timeout: float) -> None:
        """Initialize with timeout details.

        Args:
            target_instance: The instance that was targeted.
            action: The action that was being forwarded.
            timeout: The timeout value in seconds.
        """
        self.target_instance = target_instance
        self.action = action
        self.timeout = timeout
        super().__init__(
            f"Forward to {target_instance} timed out after {timeout}s for action {action}"
        )


class ForwardHTTPError(InstanceRouterError):
    """Raised when forward request returns non-2xx status.

    Indicates the target instance received the request but returned an error.
    The response_body may contain additional error details.
    """

    def __init__(
        self, target_instance: str, status_code: int, response_body: str
    ) -> None:
        """Initialize with HTTP error details.

        Args:
            target_instance: The instance that was targeted.
            status_code: The HTTP status code returned.
            response_body: The response body (may contain error details).
        """
        self.target_instance = target_instance
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(
            f"Forward to {target_instance} failed with status {status_code}"
        )
