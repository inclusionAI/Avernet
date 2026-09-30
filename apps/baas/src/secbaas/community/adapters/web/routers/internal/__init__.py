"""Internal and cache routes — re-exports for convenience."""

from secbaas.community.adapters.web.routers.internal.cache_router import (
    router as cache_router,
)
from secbaas.community.adapters.web.routers.internal.internal_health_router import (
    router as internal_health_router,
)
from secbaas.community.adapters.web.routers.internal.internal_router import (
    router as internal_router,
)

__all__ = [
    "cache_router",
    "internal_health_router",
    "internal_router",
]
