"""BCN downlink routes — re-exports for convenience."""

from secbaas.community.adapters.web.routers.bcn_downlink.bcn_router import (
    bcn_exception_handler,
)
from secbaas.community.adapters.web.routers.bcn_downlink.bcn_router import (
    router as bcn_downlink_router,
)

__all__ = [
    "bcn_downlink_router",
    "bcn_exception_handler",
]
