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

    return app