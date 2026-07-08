"""InstanceRouter configuration.

Per Microkernel Architecture Rule 14: All wiring is configuration-driven.
No hardcoded timeouts, ports, or limits in business logic.
"""

from dataclasses import dataclass


@dataclass
class InstanceRouterConfig:
    """Configuration for InstanceRouter HTTP client.

    All timeouts and limits are configurable to support different deployment
    scenarios (local development vs production).

    Attributes:
        internal_port: Target port for internal forwarding (required, no default
            — injected from module_config.web.port via DI container).
        connect_timeout: Seconds to establish connection (default: 5.0).
        read_timeout: Seconds to wait for response (default: 30.0).
        pool_timeout: Seconds to wait for connection from pool (default: 5.0).
        max_connections: Maximum total connections in pool (default: 100).
        max_keepalive: Maximum keepalive connections (default: 20).
    """

    internal_port: int
    connect_timeout: float = 5.0
    read_timeout: float = 30.0
    pool_timeout: float = 5.0
    max_connections: int = 100
    max_keepalive: int = 20

    def __post_init__(self) -> None:
        """Validate configuration values."""
        if self.connect_timeout <= 0:
            raise ValueError("connect_timeout must be positive")
        if self.read_timeout <= 0:
            raise ValueError("read_timeout must be positive")
        if self.pool_timeout <= 0:
            raise ValueError("pool_timeout must be positive")
        if self.max_connections <= 0:
            raise ValueError("max_connections must be positive")
        if self.max_keepalive <= 0:
            raise ValueError("max_keepalive must be positive")
        if self.max_keepalive > self.max_connections:
            raise ValueError("max_keepalive cannot exceed max_connections")
        if self.internal_port <= 0 or self.internal_port > 65535:
            raise ValueError("internal_port must be a valid port number")

    def get_timeout_dict(self) -> dict:
        """Return timeout configuration as dict for httpx.

        Returns:
            Dict with connect, read, and pool timeout values.
        """
        return {
            "connect": self.connect_timeout,
            "read": self.read_timeout,
            "pool": self.pool_timeout,
        }
