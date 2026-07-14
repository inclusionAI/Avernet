"""
OSS FastAPI Application Factory

Creates FastAPI application for OSS deployment.
"""
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def create_opensource_app(mode: str = None) -> FastAPI:
    """Create OSS FastAPI application.

    Args:
        mode: Provider mode (runtime, dev, test).
              If None, reads from BCSFUSE_PROVIDER_MODE env var.
              Defaults to 'dev' if not set.

    Returns:
        Configured FastAPI application instance.
    """
    from src.bootstrap.application_context import build_application_context
    from src.bootstrap.oss_business_routes import include_oss_business_routes

    # Build application context
    context = build_application_context(mode=mode)

    # CRITICAL: Share application context with fusion_dependencies to avoid
    # Qdrant embedded client lock errors (OPENCORE-P1 Phase F fix)
    # This allows services without Request access to use the shared vector_store
    # from the provider registry instead of creating duplicate QdrantClient instances.
    try:
        from src.interfaces.api.dependencies.fusion_dependencies import set_app_context
        set_app_context(context)
        logger.info("[OSS App] Application context shared with fusion_dependencies")
    except Exception as e:
        logger.warning(f"Failed to set app context in fusion_dependencies: {e}")

    # Create FastAPI app
    app = FastAPI(
        title="BCSFuse OSS",
        description="BCSFuse Open Source Deployment",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Store context in app state
    app.state.context = context

    # ========================================
    # Health Endpoints
    # ========================================
    @app.get("/health", tags=["Health"])
    async def health():
        """Health check endpoint.

        Returns shallow process health without triggering provider initialization.
        This endpoint is designed to be lightweight and fast, suitable for
        load balancers and orchestration systems.
        """
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "startup_profile": "opensource",
                "provider_mode": context.mode,
                "process_health": "alive",
            },
        )

    @app.get("/ready", tags=["Health"])
    async def ready():
        """Readiness check endpoint.

        Checks provider registry availability. Returns 503 if registry
        is not yet initialized or fails to initialize. Does not fail
        with 500 on provider errors.
        """
        try:
            # Attempt to access registry, but handle initialization failures gracefully
            provider_count = len(context.registry.keys())
            is_ready = provider_count > 0

            # DIAGNOSTIC: Check vector store instance for singleton verification
            vector_store_info = {}
            try:
                vector_store = context.registry.get('vector_store')
                if vector_store:
                    vector_store_info = {
                        "vector_store_available": True,
                        "vector_store_type": type(vector_store).__name__,
                        "vector_store_instance_id": id(vector_store),
                        "qdrant_collection_name": getattr(vector_store, 'collection_name', None),
                        "qdrant_storage_path": getattr(vector_store, 'path', None),
                    }
                else:
                    vector_store_info = {"vector_store_available": False}
            except Exception as e:
                vector_store_info = {"vector_store_error": str(e)}

            return JSONResponse(
                status_code=200 if is_ready else 503,
                content={
                    "ready": is_ready,
                    "provider_mode": context.mode,
                    "providers": provider_count,
                    **vector_store_info,  # Include vector store diagnostics
                },
            )
        except Exception as e:
            # Registry initialization failed - not ready
            logger.warning(f"Provider registry initialization failed: {e}", exc_info=True)
            return JSONResponse(
                status_code=503,
                content={
                    "ready": False,
                    "provider_mode": context.mode,
                    "error": f"Provider registry initialization failed: {type(e).__name__}",
                    "providers": 0,
                },
            )

    # ========================================
    # Business Routes (OSS-safe)
    # ========================================
    # Mount OSS business routes from separate module to avoid
    # src.interfaces.api.* import chain.
    # DO NOT import from src.interfaces.api.* because it triggers:
    # __init__.py -> app.py -> recommend_routes.py -> drm_resource.py -> Layotto init
    try:
        include_oss_business_routes(app)
        logger.info("[OSS App] OSS business routes mounted successfully")
    except Exception as e:
        # Log but don't fail - OSS routes are optional
        logger.warning(f"Failed to mount OSS business routes: {e}. Continuing with health endpoints only.", exc_info=True)

    # ========================================
    # Background Index Build (non-blocking)
    # ========================================
    # Start vector index build in a daemon thread so that search/recommend
    # requests are responsive immediately.  Results will be empty until the
    # build completes, but the API won't block for minutes.
    try:
        from src.infra.config.feature_flags import FeatureFlags
        if FeatureFlags.is_enabled("ENABLE_PROFILE_EMBEDDING_INDEX"):
            import threading

            def _background_build_index():
                import time
                start = time.time()
                try:
                    logger.info("[Startup] Background vector index build starting...")
                    from src.interfaces.api.dependencies.fusion_dependencies import (
                        _get_embedding_generator,
                        _get_profile_source,
                        _get_registry_store,
                        _get_runtime_state_store,
                    )

                    embedding_gen = _get_embedding_generator()
                    profile_src = _get_profile_source()
                    vector_store = context.registry.get("vector_store")

                    if not embedding_gen:
                        logger.warning("[Startup] Embedding generator not available — skipping background index build")
                        return
                    if not profile_src:
                        logger.warning("[Startup] Profile source not available — skipping background index build")
                        return
                    if vector_store and vector_store.size() > 0:
                        logger.info("[Startup] Vector store already has %d vectors — skipping background index build", vector_store.size())
                        return

                    from src.domain.services.profile_embedding_indexer import ProfileEmbeddingIndexer
                    from src.infra.indexing.profile_embedding_store import ProfileEmbeddingStore
                    from src.infra.config.data_paths import resolve_data_path

                    dimension = getattr(vector_store, "dimension", 4096) if vector_store else 4096
                    profile_store = ProfileEmbeddingStore(
                        dimension=dimension,
                        index_type="local",
                        db_path=resolve_data_path("data/vector_store.db"),
                        database=None,
                        datasource_name="agentclaw_ds",
                        vector_store=vector_store,
                    )
                    indexer = ProfileEmbeddingIndexer(
                        embedding_provider=embedding_gen,
                        profile_store=profile_store,
                    )

                    scan_result = profile_src.scan()
                    all_profiles = scan_result.profiles
                    logger.info("[Startup] Found %d profiles to index", len(all_profiles))

                    if not all_profiles:
                        logger.warning("[Startup] No profiles found — skipping background index build")
                        return

                    # Build worker_states for payload (availability, runtime_state)
                    worker_states = {}
                    try:
                        from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
                        from src.domain.models.worker_runtime_state import WorkerRuntimeState

                        registry_store = _get_registry_store()
                        runtime_state_store = _get_runtime_state_store()
                        active_workers = registry_store.list(lifecycle_states=[WorkerLifecycleState.ACTIVE])
                        worker_ids = [w.id for w in active_workers]
                        runtime_states = runtime_state_store.batch_get_runtime_states(worker_ids)

                        for worker in active_workers:
                            runtime_state = runtime_states.get(worker.id)
                            state_info = {
                                "availability": worker.state.availability.value,
                                "runtime_state": runtime_state.value if runtime_state else WorkerRuntimeState.OFFLINE.value,
                            }
                            worker_states[worker.id] = state_info
                            handle = worker.identity.handle
                            if handle and handle.startswith("@"):
                                worker_states[handle[1:]] = state_info
                            if hasattr(worker, "external_id") and worker.external_id:
                                worker_states[worker.external_id] = state_info

                        logger.info("[Startup] Loaded %d worker states for indexing", len(worker_states))
                    except Exception as e:
                        logger.warning("[Startup] Failed to load worker states: %s — indexing without visibility filters", e)

                    result = indexer.build_index(
                        profiles=all_profiles,
                        clear_existing=False,
                        worker_states=worker_states,
                    )

                    elapsed = time.time() - start
                    if result.indexed_count > 0:
                        logger.info(
                            "✅ [Startup] Background index build completed: indexed=%d, failed=%d, duration=%.1fs",
                            result.indexed_count, result.failed_count, elapsed,
                        )
                        # Sync vectors to service index
                        if vector_store:
                            if hasattr(vector_store, "sync_from_backend"):
                                vector_store.sync_from_backend(force=True)
                            elif hasattr(vector_store, "sync_incremental"):
                                vector_store.sync_incremental()
                            logger.info("[Startup] Vector store now has %d vectors", vector_store.size())
                    else:
                        logger.warning("⚠️ [Startup] Background index build produced no results (duration=%.1fs)", elapsed)

                except Exception as e:
                    elapsed = time.time() - start
                    logger.error("❌ [Startup] Background index build failed (duration=%.1fs): %s", elapsed, e, exc_info=True)

            thread = threading.Thread(target=_background_build_index, daemon=True, name="bg-index-build")
            thread.start()
            logger.info("[Startup] Background index build thread started — search will return empty results until complete")
    except Exception as e:
        logger.warning("[Startup] Could not start background index build: %s", e)

    return app