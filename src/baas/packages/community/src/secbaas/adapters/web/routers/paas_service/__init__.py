"""PaaS service routes — re-exports for convenience."""

from secbaas.adapters.web.routers.paas_service.device_router import (
    router as device_router,
)
from secbaas.adapters.web.routers.paas_service.local_paas_router import (
    router as local_paas_router,
)
from secbaas.adapters.web.routers.paas_service.paas_facade_router import (
    router as paas_facade_router,
)

__all__ = [
    "device_router",
    "local_paas_router",
    "paas_facade_router",
]
