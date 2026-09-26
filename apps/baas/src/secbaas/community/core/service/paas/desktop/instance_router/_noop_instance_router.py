"""NoopInstanceRouter implementation.

Per Microkernel Architecture Rule 21: Every Protocol must have Noop implementation.
Used when InstanceRouter is required by dependency injection but cross-instance
routing is not needed or not configured.
"""

from typing import Any

from ._exceptions import InstanceRouterError


class NoopInstanceRouter:
    """No-op InstanceRouter that raises NotImplementedError for all operations.

    This is the default implementation before initialization. It ensures that
    code compiles and can be imported, but will fail fast if actually used
    without proper initialization.

    Per Microkernel Rule 21: Provides a safe default that fails explicitly
    rather than silently doing nothing.
    """

    def __init__(self) -> None:
        """Initialize NoopInstanceRouter."""
        pass

    def get_instance_for(self, machine_id: str, env: str) -> None:
        """Always raises NotImplementedError.

        Args:
            machine_id: Ignored.
            env: Ignored.

        Raises:
            InstanceRouterError: Always raised with initialization message.
        """
        raise InstanceRouterError(
            "InstanceRouter not initialized. "
            "Call initialize_instance_router() during app startup."
        )

    async def route_to_instance(
        self,
        target_instance: str,
        action: str,
        machine_id: str,
        params: dict,
        request_id: str,
    ) -> Any:
        """Always raises NotImplementedError.

        Args:
            target_instance: Ignored.
            action: Ignored.
            machine_id: Ignored.
            params: Ignored.
            request_id: Ignored.

        Raises:
            InstanceRouterError: Always raised with initialization message.
        """
        raise InstanceRouterError(
            "InstanceRouter not initialized. "
            "Call initialize_instance_router() during app startup."
        )
