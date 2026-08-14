"""Session File Sharing routes — re-exports for convenience."""

from secbaas.community.adapters.web.routers.session_file_sharing.session_file_sharing_router import (
    router as session_file_sharing_router,
)

__all__ = ["session_file_sharing_router"]
