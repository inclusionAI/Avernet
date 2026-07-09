"""Admin REST API routes — re-exports for convenience."""

from secbaas.adapters.web.routers.admin.api_gateway_router import (
    router as admin_api_gateway_router,
)
from secbaas.adapters.web.routers.admin.publish_admin_router import (
    router as publish_admin_router,
)

__all__ = [
    "admin_api_gateway_router",
    "publish_admin_router",
]
