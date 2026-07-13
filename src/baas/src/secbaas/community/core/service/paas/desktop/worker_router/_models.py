"""Data models for WorkerRouter UDS infrastructure."""

import os
from dataclasses import dataclass
from typing import Any, TypedDict


@dataclass
class UDSConfig:
    """Configuration for UDS server.

    Per D-01: Socket path includes PID.
    Per D-02: Socket mode 0o600 (owner read/write only).
    """

    socket_dir: str = os.path.join(os.path.expanduser("~"), "secbaas_workers")
    socket_mode: int = 0o600
    listen_backlog: int = 128

    def __post_init__(self) -> None:
        """Validate configuration values."""
        # CR-01 Fix: Validate socket_dir to prevent directory traversal
        normalized = os.path.normpath(self.socket_dir)
        if ".." in normalized or not normalized.startswith("/"):
            raise ValueError("socket_dir must be absolute path without traversal")

        # Block dangerous system directories
        blocked_prefixes = [
            "/etc",
            "/bin",
            "/sbin",
            "/boot",
            "/dev",
            "/proc",
            "/sys",
            "/var/spool",
        ]
        for prefix in blocked_prefixes:
            if normalized.startswith(prefix):
                raise ValueError(f"socket_dir cannot be in system directory: {prefix}")

        if not isinstance(self.socket_mode, int):
            raise ValueError("socket_mode must be an integer (octal)")
        if self.listen_backlog <= 0:
            raise ValueError("listen_backlog must be positive")

    def get_socket_path(self, pid: int) -> str:
        """Get socket path for a given PID.

        Per D-01: Format is /tmp/secbaas_workers/worker_{pid}.sock
        """
        return f"{self.socket_dir}/worker_{pid}.sock"


class WorkerRouteInfo(TypedDict):
    """Route information stored in database for cross-process routing.

    Per D-07: Contains worker_pid and socket_path only.
    Per D-08: No timestamp field.
    """

    worker_pid: int
    socket_path: str


class UDSRequest(TypedDict):
    """UDS request message format.

    Per D-22: Matches WebSocket JSON format.
    """

    action: str  # "forward"
    request_id: str
    params: dict[str, Any]


class UDSResponse(TypedDict):
    """UDS response message format.

    Matches WebSocket result format for transparent forwarding.
    """

    type: str  # "result"
    request_id: str
    payload: dict[str, Any]
