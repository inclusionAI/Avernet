"""Bot service routes — re-exports for convenience."""

from secbaas.adapters.web.routers.bot_service.cmd_router import router as bot_cmd_router
from secbaas.adapters.web.routers.bot_service.http_conn_router import (
    router as bot_http_conn_router,
)
from secbaas.adapters.web.routers.bot_service.http_router import (
    router as bot_http_router,
)
from secbaas.adapters.web.routers.bot_service.management_router import (
    router as bot_management_router,
)
from secbaas.adapters.web.routers.bot_service.open_folder_router import (
    router as bot_open_folder_router,
)
from secbaas.adapters.web.routers.bot_service.publish_router import callback_router
from secbaas.adapters.web.routers.bot_service.publish_router import (
    router as publish_router,
)
from secbaas.adapters.web.routers.bot_service.wss_router import router as bot_wss_router

__all__ = [
    "bot_cmd_router",
    "bot_http_conn_router",
    "bot_http_router",
    "bot_management_router",
    "bot_open_folder_router",
    "bot_wss_router",
    "callback_router",
    "publish_router",
]
