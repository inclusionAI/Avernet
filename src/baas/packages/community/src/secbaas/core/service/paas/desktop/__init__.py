"""Desktop PaaS infrastructure components (ConnectionManager, InstanceRouter, WorkerRouter).

Moved from infra/local/ — these are desktop-device management infrastructure
consumed by LocalPaasService in the core/service/paas layer.
"""

import secbaas.core.service.paas.desktop.instance_router as instance_router

from . import _connection_manager as connection_manager
from ._connection_manager import ConnectionManager
from ._utils import get_instance_id
from .instance_router import (
    InstanceRouter,
    LocalInstanceRouter,
    MockInstanceRouter,
    NoopInstanceRouter,
    initialize_instance_router,
)

__all__ = [
    "ConnectionManager",
    "connection_manager",
    "InstanceRouter",
    "LocalInstanceRouter",
    "MockInstanceRouter",
    "NoopInstanceRouter",
    "get_instance_id",
    "instance_router",
    "initialize_instance_router",
]
