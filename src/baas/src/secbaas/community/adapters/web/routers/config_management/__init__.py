"""Config/management routes — re-exports for convenience."""

from secbaas.community.adapters.web.routers.config_management.api_gateway_router import (  # noqa: F401
    router as api_gateway_router,
)
from secbaas.community.adapters.web.routers.config_management.device_template_router import (
    router as device_template_router,
)
from secbaas.community.adapters.web.routers.config_management.qpm_config_router import (
    router as bot_qpm_router,
)
from secbaas.community.adapters.web.routers.config_management.system_config_router import (
    router as system_config_router,
)
from secbaas.community.adapters.web.routers.config_management.tenant_router import (
    router as tenant_router,
)

__all__ = [
    "api_gateway_router",
    "bot_qpm_router",
    "device_template_router",
    "system_config_router",
    "tenant_router",
]
