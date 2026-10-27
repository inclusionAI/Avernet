"""Compose the Agent application without requiring Worker/Profile/Fuse."""

from avernet_search.api.services import RetrievalService
from avernet_search.http.routes import Authenticator, create_search_router
from fastapi import FastAPI
from starlette.types import Lifespan

from agent_search.search.service import AgentSearchApplication


def create_agent_search_app(
    retrieval: RetrievalService,
    authenticate: Authenticator,
    *,
    filter_fields: frozenset[str] = frozenset(),
    lifespan: Lifespan | None = None,
    timeout_seconds: float = 2.0,
) -> FastAPI:
    """None lifespan is valid when all dependencies are externally managed."""
    service = AgentSearchApplication(retrieval, filter_fields=filter_fields)
    app = FastAPI(title="Agent Search", lifespan=lifespan)
    app.include_router(
        create_search_router(service, authenticate, timeout_seconds=timeout_seconds)
    )

    @app.get("/health")
    async def health():
        # Liveness only. Production readiness must verify its actual index.
        return {"status": "ok"}

    return app
