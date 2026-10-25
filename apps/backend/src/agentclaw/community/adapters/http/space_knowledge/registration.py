"""Space and resource route composition; defer router imports until app bootstrap."""

from fastapi import FastAPI


def mount_resource_routers(app: FastAPI) -> None:
    from agentclaw.community.adapters.http.resources import router as resources_router
    from agentclaw.community.adapters.http.session_resources import (
        internal_router,
        router as sessions_router,
    )
    from agentclaw.community.adapters.http.tc_file_upload_integrations.resource_context_router import (
        router as resource_context_router,
    )

    for router in (
        resources_router,
        sessions_router,
        internal_router,
        resource_context_router,
    ):
        app.include_router(router)


def mount_space_routers(app: FastAPI) -> None:
    from agentclaw.community.adapters.http.space_knowledge.router import (
        router as knowledge_router,
    )
    from agentclaw.community.adapters.http.spaces_internal import (
        router as internal_spaces_router,
    )
    from agentclaw.community.adapters.http.spaces_management import (
        router as management_router,
    )

    for router in (internal_spaces_router, management_router, knowledge_router):
        app.include_router(router)
