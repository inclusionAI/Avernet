"""TC composition root: owns Card synchronization and the legacy HTTP surface."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from app.tc.catalog import CardProfileStore, CardWorkerStore, TcCatalog
from app.tc.providers import embedding_identity, storage
from app.tc.search import CardProfileSource, TcSearch

logger = logging.getLogger(__name__)


def _create_profile_gateway():
    from app.adapters.http.dependencies.fusion_dependencies import _create_llm_provider
    from app.application.services.llm_gateway_service import LLMGatewayService
    from app.infra.llm.config.llm_settings import LLMSettings

    if not any(
        os.getenv(key, "").lower() == "true"
        for key in ("ENABLE_REAL_LLM", "LLM_ENABLED")
    ):
        return None
    try:
        # V1 sync waits for indexing, so use a short model timeout for TC profiles.
        settings = LLMSettings(default_timeout_ms=4000)
        if settings.base_url and settings.auth_token:
            return LLMGatewayService(_create_llm_provider(settings), settings=settings)
    except Exception:  # noqa: BLE001 -- the source Card can still be indexed
        logger.warning("TC profile model configuration unavailable")
    return None


class CardRuntimeStateStore:
    def __init__(self, catalog):
        self.catalog = catalog

    def get_runtime_state(self, worker_id):
        row = self.catalog.get(worker_id)
        return row.card.source_card.get("runtime_state", "offline") if row else None


def create_tc_app(context, *, search_router=None):
    registry = context.registry
    startup = registry.require("startup_provider")

    @asynccontextmanager
    async def lifespan(app):
        from app.adapters.http.dependencies import fusion_dependencies as dependencies
        from app.bootstrap.app_factory import _configure_fusion_dependencies

        stop = asyncio.Event()
        task = None
        runtime = None
        index = None
        await startup.initialize()
        try:
            from app.bootstrap.logging_setup import (
                configure_business_logging,
                resolve_business_log_level,
            )

            configure_business_logging(resolve_business_log_level())
            model_factory = registry.get("tc_model_factory")
            if model_factory is not None:
                model_factory()
            repository, index = storage(context)
            catalog = TcCatalog(repository)
            runtime = TcSearch(
                catalog,
                index,
                registry.require("embedding_provider"),
                registry.require("reranker_provider"),
                embedding_key=embedding_identity(
                    context.config, registry.require("embedding_provider")
                ),
                instance_id=context.config.get("instance_id", "tc"),
                profile_gateway=_create_profile_gateway(),
            )
            registry.register("tc_card_catalog", catalog)
            registry.register("tc_card_search", runtime)
            registry.register("tc_catalog_repository", repository)
            registry.register("tc_index", index)
            registry.register("tc_catalog", catalog)
            registry.register("worker_registry_store", CardWorkerStore(catalog))
            registry.register("worker_profile_content_store", CardProfileStore(catalog))
            registry.register(
                "worker_runtime_state_store", CardRuntimeStateStore(catalog)
            )
            source = CardProfileSource(catalog)
            registry.register("worker_profile_source", source)
            registry.register("tc_search", runtime)
            dependencies.reset_fusion_services()
            _configure_fusion_dependencies(context)
            dependencies.set_profile_source(source)
            await asyncio.to_thread(runtime.updates.run_once)
            task = asyncio.create_task(runtime.updates.run(stop))
            yield
        finally:
            stop.set()
            if task is not None:
                await task
            if runtime is not None:
                await asyncio.to_thread(
                    runtime.pool.shutdown, wait=True, cancel_futures=True
                )
            if index is not None:
                await asyncio.to_thread(index.close)
            temporary = registry.get("tc_temporary_directory")
            if temporary is not None:
                temporary.cleanup()
            await startup.shutdown()

    app = FastAPI(title="BCSFuse", lifespan=lifespan)
    app.state.context = context
    from app.adapters.http.fusion_parity_routes import router as fusion_router
    from app.adapters.http.verify_routes import router as verify_router
    from app.infra.public.observability.trace_middleware import TraceIdMiddleware
    from app.tc.auth import require_oss_auth
    from app.tc.compat_routes import create_compat_router
    from app.tc.routes import create_router

    app.add_middleware(TraceIdMiddleware)
    compatibility_router, core_router = create_compat_router(), create_router()
    for prefix in ("/v1", "/api/v1"):
        app.include_router(compatibility_router, prefix=prefix)
        app.include_router(core_router, prefix=prefix)
        app.include_router(
            verify_router, prefix=prefix, dependencies=[Depends(require_oss_auth)]
        )
    if os.getenv("BCSFUSE_EXPOSE_ADMIN", "false").lower() == "true":
        from app.tc.admin_routes import create_admin_router

        app.include_router(
            create_admin_router(core_router, compatibility_router), prefix="/v1/admin"
        )
    app.include_router(fusion_router, prefix="/api/v1")
    from app.tc.v2 import include_v2

    include_v2(app, search_router=search_router)

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "startup_profile": context.startup_profile,
            "provider_mode": context.mode,
            "process_health": "alive",
        }

    @app.get("/ready")
    async def ready():
        runtime = registry.get("tc_search")
        if runtime is None or not runtime.updates.ready:
            return JSONResponse(
                status_code=503,
                content={
                    "ready": False,
                    "provider_mode": context.mode,
                    "providers": len(registry.keys()),
                    "error": "INDEX_NOT_READY",
                    "v1_storage": "card",
                    "card_ready": False,
                    "environment": registry.require(
                        "tc_card_catalog"
                    ).repository.environment,
                },
            )
        return {
            "ready": True,
            "v1_storage": "card",
            "card_ready": True,
            "environment": registry.require("tc_card_catalog").repository.environment,
            "provider_mode": context.mode,
            "index_key": runtime.updates.index_key,
            "providers": len(registry.keys()),
        }

    @app.get("/providers")
    @app.get("/api/v1/providers/status")
    async def providers():
        return {"providers": registry.keys(), "mode": context.mode}

    return app
