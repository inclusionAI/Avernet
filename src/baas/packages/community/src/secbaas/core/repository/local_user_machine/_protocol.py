"""
Local user machine repository Protocol — interface contract.
"""

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from secbaas.logger import get_logger

from ._record import LocalUserMachineRecord

log = get_logger("orm-repository")


@runtime_checkable
class LocalUserMachineRepository(Protocol):
    """Protocol for local user machine repository."""

    def insert_machine(
        self,
        *,
        template_id: int,
        user_id: str,
        machine_id: str,
        machine_info: dict[str, Any] | None,
        last_heartbeat: "datetime",
        connected_server_instance: str,
        status: str,
        env: str,
    ) -> int:
        """Insert a new local user machine record. Returns the new record ID."""
        ...

    def get_by_machine_id(
        self, machine_id: str, env: str
    ) -> LocalUserMachineRecord | None:
        """Get machine record by machine_id and env.

        Uses uk_machine_env unique constraint (machine_id + env).
        """
        ...

    def list_by_user_id(self, user_id: str, env: str) -> list[LocalUserMachineRecord]:
        """List all machine records for a user in an environment.

        Uses uk_user_env unique constraint (user_id + env).
        """
        ...

    def update_heartbeat(
        self, machine_id: str, env: str, timestamp: "datetime"
    ) -> None:
        """Update heartbeat timestamp for a machine."""
        ...

    def update_status(self, machine_id: str, env: str, status: str) -> None:
        """Update status for a machine.

        Status values: ONLINE, OFFLINE, DISABLED.
        """
        ...

    def update_instance(self, machine_id: str, env: str, instance_id: str) -> None:
        """Update connected_server_instance for a machine."""
        ...

    def update_machine_info(
        self, machine_id: str, env: str, info: dict[str, Any] | None
    ) -> None:
        """Update machine_info JSON for a machine."""
        ...

    def update_route_info(self, machine_id: str, env: str, route_info: dict) -> None:
        """Update connected_route_info JSON for a machine.

        Stores worker PID and socket path for cross-process routing.
        """
        ...

    def clear_route_info(self, machine_id: str, env: str) -> None:
        """Clear connected_route_info (set to NULL) for a machine."""
        ...

    def get_route_info(self, machine_id: str, env: str) -> dict | None:
        """Get connected_route_info JSON for a machine.

        Returns None if route_info is NULL or machine not found.
        """
        ...
