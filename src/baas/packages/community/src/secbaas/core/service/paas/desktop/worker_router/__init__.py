"""Worker Router package for cross-process mng connection forwarding.

Provides UDS (Unix Domain Socket) server for multi-worker deployments.
"""

from ._exceptions import (
    ForwardUDSError,
    RouteNotFoundError,
    WorkerOfflineError,
    WorkerRouterError,
)
from ._models import (
    UDSConfig,
    UDSRequest,
    UDSResponse,
    WorkerRouteInfo,
)
from ._uds_server import UDSServer
from ._worker_router import WorkerRouter

__all__ = [
    # Main classes
    "WorkerRouter",
    "UDSServer",
    # Configuration
    "UDSConfig",
    "WorkerRouteInfo",
    # Protocol types
    "UDSRequest",
    "UDSResponse",
    # Exceptions
    "WorkerRouterError",
    "RouteNotFoundError",
    "WorkerOfflineError",
    "ForwardUDSError",
]
