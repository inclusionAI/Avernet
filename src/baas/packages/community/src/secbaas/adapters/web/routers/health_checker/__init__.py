"""Health checker routes — re-exports for convenience."""

from secbaas.adapters.web.routers.health_checker.health_checker_router import (
    router as bot_health_checker_router,
)
from secbaas.adapters.web.routers.health_checker.sandbox_device_router import (
    router as sandbox_device_router,
)

__all__ = [
    "bot_health_checker_router",
    "sandbox_device_router",
]
