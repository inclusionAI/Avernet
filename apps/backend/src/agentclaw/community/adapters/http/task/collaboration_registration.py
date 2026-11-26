"""Mount collaboration delivery surfaces at the HTTP composition root."""

from fastapi import FastAPI

from agentclaw.community.adapters.http.task.commission_a2a import (
    router as commission_a2a_router,
)
from agentclaw.community.adapters.http.task.context_collab import (
    router as context_collab_router,
)
from agentclaw.community.adapters.http.task.context_collab_methods import (
    router as context_collab_methods_router,
)
from agentclaw.community.adapters.http.task.todos.artifact_routes import (
    router as todo_artifact_router,
)


def mount_collaboration_routers(app: FastAPI) -> None:
    """Preserve the existing registration order and router contracts."""
    app.include_router(commission_a2a_router)
    app.include_router(context_collab_router)
    app.include_router(todo_artifact_router)
    app.include_router(context_collab_methods_router)
