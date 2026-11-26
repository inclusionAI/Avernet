from agentclaw.community.adapters.http.aicoding.notify_router import (
    router as notify_router,
)
from agentclaw.community.adapters.http.aicoding.work_items_router import (
    router as work_items_router,
)

__all__ = ["notify_router", "work_items_router"]


def register_routers(app) -> None:
    """Mount the AICoding routers from one composition-root call."""

    app.include_router(notify_router)
    app.include_router(work_items_router)
