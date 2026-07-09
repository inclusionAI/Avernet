"""InstanceRouter Protocol definition.

Per Microkernel Architecture Rule 20: Single protocol for local and production.
Per Microkernel Architecture Rule 21: Noop and Mock implementations for testing.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class InstanceRouter(Protocol):
    """Protocol for cross-instance HTTP request forwarding.

    The InstanceRouter discovers target instances via database lookup and forwards
    requests via HTTP. This enables distributed deployment where mng daemons connect
    to one secbaas instance but user requests may arrive at another.

    Key constraint: InstanceRouter is a transport layer component - it forwards requests
    but does not interpret command semantics. Command execution remains the
    responsibility of LocalPaasService.
    """

    async def route_to_instance(
        self,
        target_instance: str,
        action: str,
        machine_id: str,
        params: dict,
        request_id: str,
    ) -> dict:
        """Route a request to the target instance via HTTP POST.

        Args:
            target_instance: Target instance identifier (e.g., "secbaas-instance-2"
                           or IP/hostname like "10.0.0.5").
            action: The action to execute (e.g., "execute_command").
            machine_id: Target machine ID for BaaS internal routing (not passed to mng).
            params: Parameters for the action (API-specific, forwarded to mng).
            request_id: Unique request ID for tracing/correlation.

        Returns:
            Response dict from the target instance.

        Raises:
            InstanceNotFoundError: If target instance cannot be reached.
            ForwardTimeoutError: If the forward request times out.
            ForwardHTTPError: If the target returns non-2xx status.
        """
        ...

    def get_instance_for(self, machine_id: str, env: str) -> str | None:
        """Get the connected secbaas instance for a machine.

        Queries the repository for the machine's connected_server_instance.

        Args:
            machine_id: The machine identifier to look up.
            env: Environment (dev, pre, prod).

        Returns:
            The instance identifier (e.g., "secbaas-instance-2") or None if
            the machine is not connected to any instance.
        """
        ...
