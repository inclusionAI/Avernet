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


class CardRuntimeStateStore:
    def __init__(self, catalog):
        self.catalog = catalog

    def get_runtime_state(self, worker_id):
        row = self.catalog.get(worker_id)
        return row.card.source_card.get("runtime_state", "offline") if row else None


def create_tc_app(context, *, search_router=None):
    registry = context.registry
    startup = registry.require("startup_provider")
    v1_storage = os.getenv(
        "BCSFUSE_TC_V1_STORAGE", context.config.get("tc.v1_storage", "legacy")
    )
    if v1_storage not in {"legacy", "card"}:
        raise ValueError("TC v1 storage must be legacy or card")

    @asynccontextmanager
    async def lifespan(app):
        from app.adapters.http.dependencies import fusion_dependencies as dependencies
        from app.bootstrap.app_factory import _configure_fusion_dependencies

        stop = asyncio.Event()
        tasks = []
        runtimes = []
        legacy_stores = ()
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
            card_catalog = TcCatalog(repository)
            card_runtime = TcSearch(
                card_catalog,
                index,
                registry.require("embedding_provider"),
                registry.require("reranker_provider"),
                embedding_key=embedding_identity(
                    context.config, registry.require("embedding_provider")
                ),
                instance_id=context.config.get("instance_id", "tc"),
            )
            runtimes.append(card_runtime)
            registry.register("tc_card_catalog", card_catalog)
            registry.register("tc_card_search", card_runtime)
            catalog, runtime = card_catalog, card_runtime
            if v1_storage == "legacy":
                from app.tc.legacy_catalog import LegacyCatalog
                from app.tc.legacy_index import LegacyRetrievalIndex, LegacyUpdates
                from app.tc.legacy_providers import storage as old_storage

                legacy_stores = old_storage(context)
                workers, profiles, states, vectors, transaction = legacy_stores
                if (
                    vectors.dimension
                    != registry.require("embedding_provider").dimension
                ):
                    raise ValueError(
                        "legacy vector dimension differs from TC embedding"
                    )
                catalog = LegacyCatalog(workers, profiles, states, transaction)
                updates = LegacyUpdates(
                    catalog, vectors, registry.require("embedding_provider")
                )
                await asyncio.to_thread(updates.restore)
                runtime = TcSearch(
                    catalog,
                    vectors,
                    registry.require("embedding_provider"),
                    registry.require("reranker_provider"),
                    embedding_key=embedding_identity(
                        context.config, registry.require("embedding_provider")
                    ),
                    instance_id=context.config.get("instance_id", "tc"),
                    updates=updates,
                    retrieval_index=LegacyRetrievalIndex(catalog, vectors, updates),
                )
                runtimes.append(runtime)
                registry.register("tc_legacy_vectors", vectors)
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
            for service in runtimes:
                await asyncio.to_thread(service.updates.run_once)
                tasks.append(asyncio.create_task(service.updates.run(stop)))
            yield
        finally:
            stop.set()
            for task in tasks:
                await task
            for service in runtimes:
                await asyncio.to_thread(
                    service.pool.shutdown, wait=True, cancel_futures=True
                )
            if index is not None:
                await asyncio.to_thread(index.close)
            for store in reversed(legacy_stores):
                close = getattr(store, "close", None)
                if close is not None:
                    await asyncio.to_thread(close)
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
        card = registry.get("tc_card_search")
        v1_ready = runtime is not None and runtime.updates.ready
        if v1_ready and v1_storage == "legacy":
            try:
                await asyncio.to_thread(runtime.index.check_ready)
            except Exception:  # noqa: BLE001 -- probes normalize backend failures without exposing credentials
                logger.warning("TC legacy index readiness probe failed")
                v1_ready = False
        if not v1_ready:
            return JSONResponse(
                status_code=503,
                content={
                    "ready": False,
                    "provider_mode": context.mode,
                    "providers": len(registry.keys()),
                    "error": "INDEX_NOT_READY",
                    "v1_storage": v1_storage,
                    "card_ready": card is not None and card.updates.ready,
                    "environment": registry.require(
                        "tc_card_catalog"
                    ).repository.environment,
                },
            )
        return {
            "ready": True,
            "v1_storage": v1_storage,
            "card_ready": card is not None and card.updates.ready,
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
