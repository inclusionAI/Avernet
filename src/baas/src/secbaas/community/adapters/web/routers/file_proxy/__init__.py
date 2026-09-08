"""Session file-transfer proxy routes — re-exports for convenience."""

from secbaas.community.adapters.web.routers.file_proxy.file_transfer_proxy_router import (
    router as file_transfer_proxy_router,
)

__all__ = ["file_transfer_proxy_router"]
