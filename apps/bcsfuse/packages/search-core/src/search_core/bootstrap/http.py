"""Compose the Agent application without requiring Worker/Profile/Fuse."""

from collections.abc import Callable

from fastapi import FastAPI
from starlette.types import Lifespan

from search_core.adapters.http.admission import SearchAdmissionMiddleware
from search_core.adapters.http.routes import (
    Authenticator,
    ResultBody,
    SearchBody,
    create_search_router,
)
from search_core.api.services import RetrievalService
from search_core.application.admission import SearchAdmission
from search_core.application.emergency import EmergencyControl
from search_core.application.search import CatalogSearchApplication
from search_core.config.search import DEFAULT_SEARCH


def create_search_app(
    retrieval: RetrievalService,
    authenticate: Authenticator,
    *,
    filter_fields: frozenset[str] = frozenset(),
    lifespan: Lifespan | None = None,
    timeout_seconds: float = 2.0,
    max_inflight_requests: int = DEFAULT_SEARCH.max_inflight_requests,
    emergency: EmergencyControl | None = None,
    trace_id_provider: Callable[[], str] | None = None,
    request_model=SearchBody,
    result_mapper=None,
    response_model=ResultBody,
    operations=("search", "recommend"),
) -> FastAPI:
    """None lifespan is valid when all dependencies are externally managed."""
    if type(max_inflight_requests) is not int or max_inflight_requests < 0:
        raise ValueError("max_inflight_requests must be a nonnegative integer")
    service = CatalogSearchApplication(
        retrieval,
        filter_fields=filter_fields,
        request_timeout=timeout_seconds,
        emergency=emergency,
        trace_id_provider=trace_id_provider,
    )
    app = FastAPI(title="Agent Search", lifespan=lifespan)
    admission = SearchAdmission(max_inflight_requests, emergency)
    app.state.search_admission = admission
    if max_inflight_requests or emergency is not None:
        app.add_middleware(
            SearchAdmissionMiddleware,
            admission=admission,
        )
    app.state.search_application = service
    app.include_router(
        create_search_router(
            service,
            authenticate,
            timeout_seconds=timeout_seconds,
            request_model=request_model,
            result_mapper=result_mapper,
            response_model=response_model,
            operations=operations,
        )
    )

    @app.get("/health")
    async def health():
        # Liveness only. Production readiness must verify its actual index.
        return {"status": "ok"}

    return app
