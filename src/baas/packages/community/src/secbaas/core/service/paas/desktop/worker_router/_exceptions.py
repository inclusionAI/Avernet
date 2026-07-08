"""Exception hierarchy for WorkerRouter.

Follows pattern from instance_router.exceptions for consistency.
"""


class WorkerRouterError(Exception):
    """Base exception for worker routing failures."""

    pass


class RouteNotFoundError(WorkerRouterError):
    """Raised when no route info exists for machine.

    Indicates machine is connected to this instance but route_info
    has not been written yet (possible on startup race).
    """

    def __init__(self, machine_id: str) -> None:
        self.machine_id = machine_id
        super().__init__(f"No route info found for machine {machine_id}")


class WorkerOfflineError(WorkerRouterError):
    """Raised when target worker process is offline or UDS connection fails.

    Per D-16: Maps to WORKER_OFFLINE error code at upper layers.

    Attributes:
        machine_id: The target machine that could not be reached
        socket_path: The UDS socket path that was attempted
        reason: Specific failure reason (connection_refused, timeout, etc.)
        original_error: The original exception that caused this error, if any
    """

    def __init__(
        self,
        machine_id: str,
        socket_path: str,
        reason: str = "connection failed",
        original_error: Exception | None = None,
    ) -> None:
        self.machine_id = machine_id
        self.socket_path = socket_path
        self.reason = reason
        self.original_error = original_error

        message = (
            f"Worker offline for machine {machine_id}: "
            f"socket={socket_path}, reason={reason}"
        )
        super().__init__(message)


class ForwardUDSError(WorkerRouterError):
    """Raised when UDS forward operation fails.

    Used in Phase 32 for actual UDS forwarding errors.
    """

    def __init__(self, target_pid: int, action: str, error: str) -> None:
        self.target_pid = target_pid
        self.action = action
        self.error = error
        super().__init__(
            f"UDS forward to worker {target_pid} failed for action '{action}': {error}"
        )
