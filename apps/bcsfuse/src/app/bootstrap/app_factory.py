"""Transport composition for a fully assembled BCSFuse application context."""

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.index_backfill_status import IndexBackfillStatus
from app.bootstrap.logging_setup import (
    configure_business_logging,
    resolve_business_log_level,
)
from app.bootstrap.oss_business_routes import include_oss_business_routes
from app.bootstrap.readiness_probe import ReadinessProbeCache
from app.bootstrap.route_mount_contract import (
    validate_required_oss_business_routes,
)

logger = logging.getLogger(__name__)


def _configure_fusion_dependencies(context: ApplicationContext) -> None:
    from app.application.services.bot_fuse.fused_profile_storage_service import (
        FusedProfileStorageService,
    )
    from app.adapters.http.dependencies import fusion_dependencies

    fusion_dependencies.set_app_context(context)
    fused_store = context.registry.get("fused_profile_store")
    storage_service = None
    if fused_store is not None:
        storage_service = FusedProfileStorageService(repository=fused_store)
        logger.info(
            "[App Factory] Composed fused_profile_store registered: %s",
            type(fused_store).__name__,
        )
    fusion_dependencies.set_fused_profile_storage_service(storage_service)


_INDEX_METRICS = None
_OWNER_CHANGE_LAST: dict[tuple[str, str], int] = {}


def _get_index_metrics():
    """Create index gauges once; labels follow the active collection owner."""
    global _INDEX_METRICS
    if _INDEX_METRICS is None:
        from prometheus_client import Counter, Gauge

        labels = ["collection", "owner"]
        _INDEX_METRICS = {
            "ready": Gauge(
                "bcsfuse_index_ready",
                "Whether the local index replica is ready to serve reads",
                labels,
            ),
            "stale": Gauge(
                "bcsfuse_index_stale",
                "Whether the local index replica is stale",
                labels,
            ),
            "lag_seconds": Gauge(
                "bcsfuse_index_lag_seconds",
                "Index revision lag in seconds",
                labels,
            ),
            "degraded": Gauge(
                "bcsfuse_index_degraded",
                "Whether the index is serving with a degraded dependency",
                labels,
            ),
            "owner_changes": Counter(
                "bcsfuse_index_owner_changes_total",
                "Index owner changes observed by this replica",
                labels,
            ),
            "owner_takeover_seconds": Gauge(
                "bcsfuse_index_owner_takeover_seconds",
                "Seconds taken by the most recently observed index owner change",
                labels,
            ),
        }
    return _INDEX_METRICS


async def _index_readiness(context: ApplicationContext) -> tuple[bool, dict]:
    provider_count = len(context.registry.keys())
    is_ready = provider_count > 0
    vector_store_info: dict = {}

    try:
        vector_store = context.registry.get("vector_store")
    except Exception as error:
        # Do not leak endpoint details from a provider-registry implementation.
        return is_ready, {"vector_store_error": type(error).__name__}

    if vector_store is None:
        return is_ready, {"vector_store_available": False}

    partial_readiness: dict[str, bool] | None = None
    try:
        partial_readiness_probe = getattr(
            vector_store, "index_dependency_readiness", None
        )
        if partial_readiness_probe is not None:
            from starlette.concurrency import run_in_threadpool

            partial_readiness = dict(
                await run_in_threadpool(partial_readiness_probe)
            )
        else:
            check_ready = getattr(vector_store, "check_ready", None)
            if check_ready is not None:
                from starlette.concurrency import run_in_threadpool

                store_ready = await run_in_threadpool(check_ready)
                if store_ready is False:
                    is_ready = False
    except Exception as error:
        is_ready = False
        return is_ready, {"vector_store_error": type(error).__name__}

    if partial_readiness is not None:
        dense_ready = bool(partial_readiness.get("dense_qdrant_ready"))
        keyword_ready = bool(partial_readiness.get("keyword_qdrant_ready"))
        # An explicit false from the wrapper records durable index-state failure
        # even while a particular Qdrant replica answers a collection probe.
        index_state_ready = bool(partial_readiness.get("index_state_ready", True))
        is_ready = is_ready and dense_ready and index_state_ready

    connection = getattr(vector_store, "connection", None)
    index_stale = bool(getattr(vector_store, "index_stale", False))
    startup_rebuild_failed = bool(
        getattr(vector_store, "startup_rebuild_failed", False)
    )
    if index_stale:
        is_ready = False

    from app.infra.config.feature_flags import FeatureFlags

    profile_embedding_index_enabled = (
        FeatureFlags.is_profile_embedding_index_enabled()
    )
    backfill_status = context.registry.get("index_backfill_status")
    backfill = (
        backfill_status.snapshot()
        if backfill_status is not None
        else {"running": False, "failed": False, "error_type": None}
    )
    if backfill["running"] or backfill["failed"]:
        is_ready = False
    index_size: int | None = None
    index_size_unavailable = False
    size_probe = getattr(vector_store, "size", None)
    if callable(size_probe):
        try:
            index_size = int(await run_in_threadpool(size_probe))
            if index_size < 0:
                raise ValueError("index size cannot be negative")
        except Exception:
            index_size = None
            index_size_unavailable = True
            if profile_embedding_index_enabled:
                is_ready = False
        else:
            if profile_embedding_index_enabled and index_size == 0:
                is_ready = False
    if startup_rebuild_failed:
        is_ready = False
    literal_fallback = getattr(vector_store, "literal_fallback_index", None)
    literal_fallback_source = getattr(literal_fallback, "source", None)
    literal_fallback_version = getattr(
        literal_fallback,
        "snapshot_index_version",
        None,
    )
    literal_fallback_profiles = getattr(literal_fallback, "profile_count", None)
    literal_fallback_publish_error = getattr(
        vector_store,
        "snapshot_publish_error_type",
        None,
    )
    vector_store_info: dict = {
        "vector_store_available": True,
        "qdrant_mode": getattr(connection, "mode", None),
        "keyword_qdrant_mode": getattr(
            getattr(vector_store, "keyword_connection", None), "mode", None
        ),
        "vector_store_type": type(vector_store).__name__,
        "vector_store_instance_id": id(vector_store),
        "qdrant_collection_name": getattr(vector_store, "collection_name", None),
        "qdrant_storage_path": getattr(vector_store, "path", None),
        "index_owner": getattr(vector_store, "index_owner", None),
        "index_version": getattr(vector_store, "index_version", None),
        "index_lag_seconds": getattr(vector_store, "index_lag_seconds", None),
        "index_stale": index_stale,
        "startup_rebuild_failed": startup_rebuild_failed,
        "startup_rebuild_error_type": getattr(
            vector_store, "startup_rebuild_error_type", None
        ),
        "stale_after_seconds": getattr(
            vector_store, "stale_after_seconds", None
        ),
        "index_owner_changes": getattr(vector_store, "index_owner_changes", None),
        "index_owner_takeover_seconds": getattr(
            vector_store, "index_owner_takeover_seconds", None
        ),
        "index_state_unavailable": bool(
            getattr(vector_store, "index_state_unavailable", False)
        ),
        "index_size": index_size,
        "index_size_unavailable": index_size_unavailable,
        "index_backfill_running": bool(backfill["running"]),
        "index_backfill_failed": bool(backfill["failed"]),
        "index_backfill_error_type": (
            backfill["error_type"]
            if backfill["failed"]
            else None
        ),
        "dense_index_empty": bool(
            profile_embedding_index_enabled
            and index_size == 0
        ),
        "read_only": bool(getattr(vector_store, "read_only", False)),
        "literal_fallback_source": literal_fallback_source,
        "literal_fallback_index_version": literal_fallback_version,
        "literal_fallback_profiles": literal_fallback_profiles,
        "literal_fallback_publish_error_type": literal_fallback_publish_error,
        "embedding_model": getattr(vector_store, "embedding_model", None),
        "embedding_dimension": getattr(
            vector_store, "embedding_dimension", None
        ),
        "embedding_index_signature_ready": bool(
            partial_readiness.get("embedding_index_signature_ready", True)
            if partial_readiness is not None
            else True
        ),
        "embedding_index_signature_mismatch": bool(
            getattr(
                vector_store,
                "embedding_index_signature_mismatch",
                False,
            )
        ),
        "embedding_index_signature_unavailable": bool(
            getattr(
                vector_store,
                "embedding_index_signature_unavailable",
                False,
            )
        ),
    }
    if partial_readiness is not None:
        dense_ready = bool(partial_readiness.get("dense_qdrant_ready"))
        keyword_ready = bool(partial_readiness.get("keyword_qdrant_ready"))
        vector_store_info.update(
            {
                "dense_qdrant_available": dense_ready,
                "keyword_qdrant_available": keyword_ready,
                "dense_qdrant_unavailable": not dense_ready,
                "keyword_qdrant_unavailable": not keyword_ready,
                "degraded": not (dense_ready and keyword_ready),
            }
        )
    if vector_store_info.get("index_stale", False):
        is_ready = False
    if vector_store_info.get("index_state_unavailable", False):
        is_ready = False
    if vector_store_info.get("startup_rebuild_failed", False):
        vector_store_info["degraded"] = True
    if literal_fallback_publish_error:
        vector_store_info["degraded"] = True
    if backfill["running"] or backfill["failed"]:
        vector_store_info["degraded"] = True
    return is_ready, vector_store_info


def _register_health_routes(
    app: FastAPI,
    context: ApplicationContext,
    readiness_cache: ReadinessProbeCache | None = None,
) -> None:
    readiness_cache = readiness_cache or ReadinessProbeCache()
    @app.get("/health", tags=["Health"])
    async def health():
        """Return shallow process health without initializing providers."""
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "startup_profile": context.startup_profile,
                "provider_mode": context.mode,
                "process_health": "alive",
            },
        )

    @app.get("/ready", tags=["Health"])
    async def ready():
        """Return provider readiness without leaking provider failure details."""
        try:
            is_ready, vector_store_info = await readiness_cache.resolve(
                lambda: _index_readiness(context)
            )
            provider_count = len(context.registry.keys())
            return JSONResponse(
                status_code=200 if is_ready else 503,
                content={
                    "ready": is_ready,
                    "provider_mode": context.mode,
                    "providers": provider_count,
                    **vector_store_info,
                },
            )
        except Exception as error:
            logger.warning(
                "Provider registry initialization failed: %s",
                error,
                exc_info=True,
            )
            return JSONResponse(
                status_code=503,
                content={
                    "ready": False,
                    "provider_mode": context.mode,
                    "error": (
                        "Provider registry initialization failed: "
                        f"{type(error).__name__}"
                    ),
                    "providers": 0,
                },
            )

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        """Expose current index ownership and staleness in Prometheus text format."""
        from fastapi import Response
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        is_ready, info = await readiness_cache.resolve(
            lambda: _index_readiness(context)
        )
        collection = str(info.get("qdrant_collection_name") or "unknown")
        owner = str(info.get("index_owner") or "unknown")
        gauges = _get_index_metrics()
        labels = {"collection": collection, "owner": owner}
        gauges["ready"].labels(**labels).set(1 if is_ready else 0)
        gauges["stale"].labels(**labels).set(
            1 if bool(info.get("index_stale", False)) else 0
        )
        try:
            lag = float(info.get("index_lag_seconds") or 0)
        except (TypeError, ValueError):
            lag = 0
        gauges["lag_seconds"].labels(**labels).set(max(lag, 0))
        gauges["degraded"].labels(**labels).set(
            1 if bool(info.get("degraded", False)) else 0
        )
        label_key = (collection, owner)
        current_owner_changes = int(info.get("index_owner_changes") or 0)
        owner_change_delta = max(
            0, current_owner_changes - _OWNER_CHANGE_LAST.get(label_key, 0)
        )
        gauges["owner_changes"].labels(**labels).inc(owner_change_delta)
        _OWNER_CHANGE_LAST[label_key] = current_owner_changes
        gauges["owner_takeover_seconds"].labels(**labels).set(
            max(0.0, float(info.get("index_owner_takeover_seconds") or 0))
        )
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def create_bcsfuse_app(
    context: ApplicationContext, *, search_router: APIRouter | None = None,
) -> FastAPI:
    """Create the FastAPI application from an explicitly assembled context."""
    context.registry.require("config")
    context.registry.require("secret_provider")
    startup_provider = context.registry.require("startup_provider")
    business_log_level = resolve_business_log_level()
    configure_business_logging(business_log_level)
    profile_store = context.registry.get("worker_profile_content_store")
    if profile_store is not None:
        from app.bootstrap.profile_store_compat import (
            ensure_profile_route_compatibility,
        )

        context.registry.register(
            "worker_profile_content_store",
            ensure_profile_route_compatibility(profile_store),
        )
    background_index_target = None
    shared_cache_reader = None
    backfill_status = context.registry.get("index_backfill_status")
    if backfill_status is None:
        backfill_status = IndexBackfillStatus()
        context.registry.register("index_backfill_status", backfill_status)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        background_thread = None
        background_thread_started = False
        await startup_provider.initialize()
        try:
            # A hosting SDK may reconfigure logging during provider startup.
            configure_business_logging(business_log_level)
            logger.info(
                "[Startup] business_log_level=%s (LOG_LEVEL override; restart to apply)",
                logging.getLevelName(business_log_level),
            )
            if background_index_target is not None:
                import threading

                def run_background_index_target() -> None:
                    try:
                        background_index_target()
                    except Exception as error:
                        backfill_status.mark_failed(type(error).__name__)
                        raise
                    else:
                        backfill_status.mark_completed()

                backfill_status.mark_started()
                background_thread = threading.Thread(
                    target=run_background_index_target,
                    daemon=True,
                    name="bg-index-build",
                )
                background_thread.start()
                background_thread_started = True
                logger.info(
                    "[Startup] Background index build thread started — "
                    "the pod remains not-ready until reconciliation completes"
                )
            yield
        finally:
            try:
                if background_thread_started:
                    background_thread.join()
            finally:
                try:
                    if shared_cache_reader is not None:
                        await shared_cache_reader.close()
                finally:
                    try:
                        for shared_cache_writer in shared_cache_writers:
                            await shared_cache_writer.close()
                    finally:
                        await startup_provider.shutdown()

    # CRITICAL: Share application context with fusion_dependencies to avoid
    # Qdrant embedded client lock errors (OPENCORE-P1 Phase F fix)
    # This allows services without Request access to use the shared vector_store
    # from the provider registry instead of creating duplicate QdrantClient instances.
    _configure_fusion_dependencies(context)
    logger.info("[App Factory] Application context shared with fusion_dependencies")

    # Create FastAPI app
    app = FastAPI(
        title="BCSFuse",
        description="BCSFuse Service",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    readiness_cache = ReadinessProbeCache()
    app.state.readiness_probe_cache = readiness_cache

    from app.bootstrap.search_capacity import (
        SearchCapacityMiddleware,
        SearchCapacitySettings,
    )
    from app.infra.public.observability.trace_middleware import TraceIdMiddleware

    capacity_settings = SearchCapacitySettings.from_context(context)
    from app.adapters.http.search_threadpool import (
        configure_search_thread_capacity,
    )

    configure_search_thread_capacity(capacity_settings.max_inflight)
    app.add_middleware(
        SearchCapacityMiddleware,
        settings=capacity_settings,
    )
    app.state.search_capacity_max_inflight = capacity_settings.max_inflight

    from app.adapters.http.shared_response_cache_reader import (
        BoundedSharedResponseCacheReader,
    )
    from app.application.agent_search_cache import AgentSearchResultCache
    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
    )
    from app.adapters.http.search_response_cache import (
        SearchResponseCacheMiddleware,
    )
    from app.bootstrap.oss_business_routes import require_oss_auth

    result_cache = AgentSearchResultCache.from_context(context)
    try:
        vector_store = context.registry.get("vector_store")
    except Exception:
        vector_store = None
    local_retrieval_snapshot = getattr(
        vector_store, "literal_fallback_index", None
    )
    from app.application.agent_search_resilience_cache import (
        AgentSearchResilienceCache,
    )
    from app.application.services.agent_search_execution_budget import (
        AgentSearchExecutionBudget,
    )

    resilience_cache = AgentSearchResilienceCache.from_context(context)
    execution_budget = AgentSearchExecutionBudget.from_context(context)
    from app.application.services.retrieval_dependency_circuit import (
        RetrievalDependencyCircuit,
    )

    retrieval_dependency_circuit = RetrievalDependencyCircuit(
        outage_probe_seconds=1.0
    )
    shared_response_cache = context.registry.get("shared_response_cache")
    shared_cache_reader = None
    shared_cache_writers: list[Any] = []
    search_cache_writer = None
    recommend_cache_writer = None
    if shared_response_cache is not None:
        from app.adapters.http.shared_response_cache_writer import (
            BoundedSharedResponseCacheWriter,
            DEFAULT_L2_WRITE_TIMEOUT_SECONDS,
            DEFAULT_L2_WRITE_TTL_SECONDS,
        )

        shared_cache_reader = BoundedSharedResponseCacheReader(
            shared_response_cache=shared_response_cache
        )
        search_cache_writer = BoundedSharedResponseCacheWriter(
            shared_response_cache=shared_response_cache,
            operation="search",
            ttl_seconds=DEFAULT_L2_WRITE_TTL_SECONDS,
            timeout_seconds=DEFAULT_L2_WRITE_TIMEOUT_SECONDS,
        )
        recommend_cache_writer = BoundedSharedResponseCacheWriter(
            shared_response_cache=shared_response_cache,
            operation="recommend",
            ttl_seconds=DEFAULT_L2_WRITE_TTL_SECONDS,
            timeout_seconds=DEFAULT_L2_WRITE_TIMEOUT_SECONDS,
        )
        shared_cache_writers = [search_cache_writer, recommend_cache_writer]
    app.state.agent_search_result_cache = result_cache
    app.state.local_retrieval_snapshot = local_retrieval_snapshot
    app.state.agent_search_resilience_cache = resilience_cache
    app.state.agent_search_execution_budget = execution_budget
    app.state.retrieval_dependency_circuit = retrieval_dependency_circuit
    app.state.shared_response_cache = shared_response_cache
    app.state.shared_cache_reader = shared_cache_reader
    app.state.shared_cache_writers = shared_cache_writers
    app.add_middleware(
        SearchResponseCacheMiddleware,
        result_cache=result_cache,
        require_auth=require_oss_auth,
        shared_response_cache=shared_response_cache,
        shared_cache_reader=shared_cache_reader,
        shared_cache_writer=search_cache_writer,
    )
    app.add_middleware(
        RecommendationResponseCacheMiddleware,
        result_cache=result_cache,
        require_auth=require_oss_auth,
        shared_response_cache=shared_response_cache,
        shared_cache_reader=shared_cache_reader,
        shared_cache_writer=recommend_cache_writer,
    )
    app.add_middleware(TraceIdMiddleware)

    # Store context in app state
    app.state.context = context

    # The host explicitly supplies a shared router with its authenticated
    # business service. Existing callers keep the complete TC v1 surface.
    if search_router is not None:
        app.include_router(search_router)

    _register_health_routes(app, context, readiness_cache)

    # ========================================
    # Business Routes (OSS-safe)
    # ========================================
    # Mount OSS business routes from separate module to avoid
    # app.adapters.http.* import chain.
    # DO NOT import from app.adapters.http.* because it triggers:
    # __init__.py -> app.py -> recommend_routes.py -> drm_resource.py -> Layotto init
    from app.bootstrap.oss_worker_lifecycle_routes import (
        external_router as worker_lifecycle_external_router,
        router as worker_lifecycle_router,
    )

    # Register the authenticated lifecycle DELETE before the legacy R3
    # worker router, which exposes the same path with best-effort cleanup.
    # Starlette resolves duplicate routes in registration order.
    app.include_router(worker_lifecycle_router)
    app.include_router(worker_lifecycle_external_router)
    include_oss_business_routes(app)
    validate_required_oss_business_routes(app)
    logger.info("[App Factory] Business routes mounted successfully")

    # ========================================
    # Background Index Build (non-blocking)
    # ========================================
    # Start vector index build in a daemon thread so that search/recommend
    # requests are responsive immediately.  Results will be empty until the
    # build completes, but the API won't block for minutes.
    try:
        from app.infra.config.feature_flags import FeatureFlags

        if FeatureFlags.is_enabled("ENABLE_PROFILE_EMBEDDING_INDEX"):

            def _background_build_index():
                import time

                start = time.time()
                try:
                    logger.info("[Startup] Background vector index build starting...")
                    from app.adapters.http.dependencies.fusion_dependencies import (
                        _get_embedding_generator,
                        _get_profile_source,
                        _get_registry_store,
                        _get_runtime_state_store,
                    )

                    embedding_gen = _get_embedding_generator()
                    profile_src = _get_profile_source()
                    vector_store = context.registry.get("vector_store")

                    if not embedding_gen:
                        logger.warning(
                            "[Startup] Embedding generator not available — skipping background index build"
                        )
                        return
                    if not profile_src:
                        logger.warning(
                            "[Startup] Profile source not available — skipping background index build"
                        )
                        return
                    # A non-empty vector index is not proof that indexing
                    # completed. Check exact expected fragment IDs after scan.

                    from app.domain.services.profile_embedding_indexer import (
                        ProfileEmbeddingIndexer,
                    )
                    from app.infra.config.data_paths import resolve_data_path
                    from app.infra.indexing.profile_embedding_store import (
                        ProfileEmbeddingStore,
                    )

                    dimension = (
                        getattr(vector_store, "dimension", 4096)
                        if vector_store
                        else 4096
                    )
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
                    logger.info(
                        "[Startup] Found %d profiles to index", len(all_profiles)
                    )

                    if not all_profiles:
                        logger.warning(
                            "[Startup] No profiles found — skipping background index build"
                        )
                        return

                    indexed_ids: list[str] | set[str] | None = None
                    if vector_store and vector_store.size() > 0:
                        get_vector_ids = getattr(
                            vector_store, "get_vector_ids", None
                        )
                        if callable(get_vector_ids):
                            try:
                                indexed_ids = set(get_vector_ids())
                            except Exception as error:
                                logger.warning(
                                    "[Startup] Failed to inspect existing index for completion: %s; scheduling a full rebuild",
                                    error,
                                )
                                indexed_ids = None

                    if vector_store and vector_store.size() > 0:
                        if indexed_ids is not None:
                            original_profile_count = len(all_profiles)
                            all_profiles = indexer.profiles_missing_fragments(
                                all_profiles, indexed_ids
                            )
                            if not all_profiles:
                                logger.info(
                                    "[Startup] Existing vector index is complete (%d vectors) — skipping background index build",
                                    vector_store.size(),
                                )
                                return
                            logger.warning(
                                "[Startup] Existing vector index is partial: vectors=%d, expected_profiles=%d, profiles_to_backfill=%d",
                                vector_store.size(),
                                original_profile_count,
                                len(all_profiles),
                            )
                        else:
                            logger.warning(
                                "[Startup] Existing vector index size=%d but fragment IDs cannot be inspected — scheduling a full rebuild",
                                vector_store.size(),
                            )

                    # Build worker_states for payload (availability, runtime_state)
                    worker_states = {}
                    try:
                        from app.domain.models.worker_lifecycle_state import (
                            WorkerLifecycleState,
                        )
                        from app.domain.models.worker_runtime_state import (
                            WorkerRuntimeState,
                        )

                        registry_store = _get_registry_store()
                        runtime_state_store = _get_runtime_state_store()
                        active_workers = registry_store.list(
                            lifecycle_states=[WorkerLifecycleState.ACTIVE]
                        )
                        worker_ids = [w.id for w in active_workers]
                        runtime_states = runtime_state_store.batch_get_runtime_states(
                            worker_ids
                        )

                        for worker in active_workers:
                            runtime_state = runtime_states.get(worker.id)
                            state_info = {
                                "availability": worker.state.availability.value,
                                "runtime_state": runtime_state.value
                                if runtime_state
                                else WorkerRuntimeState.OFFLINE.value,
                            }
                            worker_states[worker.id] = state_info
                            handle = worker.identity.handle
                            if handle and handle.startswith("@"):
                                worker_states[handle[1:]] = state_info
                            if hasattr(worker, "external_id") and worker.external_id:
                                worker_states[worker.external_id] = state_info

                        logger.info(
                            "[Startup] Loaded %d worker states for indexing",
                            len(worker_states),
                        )
                    except Exception as e:
                        logger.warning(
                            "[Startup] Failed to load worker states: %s — indexing without visibility filters",
                            e,
                        )

                    result = indexer.build_index(
                        profiles=all_profiles,
                        clear_existing=False,
                        worker_states=worker_states,
                    )

                    if result.failed_count > 0:
                        raise RuntimeError(
                            "profile embedding index backfill did not "
                            f"complete: indexed={result.indexed_count}, "
                            f"failed={result.failed_count}"
                        )

                    elapsed = time.time() - start
                    if result.indexed_count > 0:
                        logger.info(
                            "✅ [Startup] Background index build completed: indexed=%d, failed=%d, duration=%.1fs",
                            result.indexed_count,
                            result.failed_count,
                            elapsed,
                        )
                        # ProfileEmbeddingStore::upsert_embeddings writes
                        # through the composed vector store. An additional
                        # incremental sync would duplicate durable work and is
                        # not supported by every durable adapter.
                        if vector_store:
                            logger.info(
                                "[Startup] Vector store now has %d vectors",
                                vector_store.size(),
                            )
                    else:
                        logger.warning(
                            "⚠️ [Startup] Background index build produced no results (duration=%.1fs)",
                            elapsed,
                        )

                except Exception as e:
                    elapsed = time.time() - start
                    logger.error(
                        "❌ [Startup] Background index build failed (duration=%.1fs): %s",
                        elapsed,
                        e,
                        exc_info=True,
                    )
                    raise

            background_index_target = _background_build_index
    except Exception as e:
        logger.warning("[Startup] Could not configure background index build: %s", e)

    return app
